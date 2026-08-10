import re
import uuid

from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone

from clinics.models import Clinic
from .crypto import encrypt_credential, decrypt_credential


class TISSGatewayProvider(models.TextChoices):
    """
    BACFF-014: qual client SOAP usar para esta operadora. Cada valor aqui
    DEVE ter uma entrada correspondente em `tiss/providers/_PROVIDERS` —
    há teste de integridade que quebra o build se um valor for adicionado
    sem provider registrado (§8.6 do documento de arquitetura). Novas
    operadoras/hubs especulativos (Sulamérica, Porto Seguro etc.) NÃO
    entram aqui até terem client próprio implementado E homologado.

    D3 (decisão do Tech Lead, 2026-07-28): `DESCONHECIDO` é o novo default.
    Até esta arquitetura, o default era `GENERICO_ANS` — ou seja, qualquer
    config criada sem escolha explícita silenciosamente assumia falar o
    dialeto genérico ANS, que NENHUMA operadora nossa confirmou aceitar (e
    que sequer envia credencial — buraco B4 do documento). Um default que
    tenta um dialeto não confirmado contra uma operadora real produz glosa
    e retrabalho de faturamento na clínica; falhar explicitamente é
    estritamente melhor. `GENERICO_ANS` continua existindo, mas agora só
    por ESCOLHA DELIBERADA de quem confirmou a compatibilidade com a
    operadora — nunca por omissão.
    """
    DESCONHECIDO = 'desconhecido', 'Desconhecido (não confirmado — bloqueia chamada automática)'
    GENERICO_ANS = 'generico_ans', 'Genérico (padrão ANS) — só com compatibilidade confirmada'
    ORIZON = 'orizon', 'Orizon (Autorize)'


class TISSOperatorConnection(models.Model):
    """
    BACFF — correção do defeito de credencial duplicada descrito em
    `.claude/tasks/TISS-MULTI-OPERATOR-STRATEGY.md` §2: transporte + endpoint
    + credencial de UMA clínica para UM agregador/gateway (ex.: "a Orizon da
    Clínica X"). Uma clínica que fala com N operadoras reais através do MESMO
    agregador reaproveita a MESMA connection — a credencial (que é do
    agregador, não da operadora real) deixa de ser copiada N vezes.

    `TISSOperatorConfig` (abaixo) passa a ser só a particularidade da
    operadora real (registro ANS, nome) + FK para esta connection.

    login/senha NUNCA ficam em texto plano no banco — só o token Fernet.
    Não expor em __str__, admin list_display, serializers ou logs.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='tiss_operator_connections')
    endpoint_url = models.URLField(max_length=255)
    gateway_provider = models.CharField(
        max_length=20, choices=TISSGatewayProvider, default=TISSGatewayProvider.DESCONHECIDO,
        help_text=(
            'Qual client SOAP usar para este transporte. Deixe em "Desconhecido" '
            'até confirmar o dialeto contra o manual técnico oficial: nesse estado '
            'a chamada automática é bloqueada com erro explícito e a recepção usa '
            'o registro manual, em vez de mandar payload no dialeto errado.'
        ),
    )

    # Sempre armazenados cifrados (Fernet) — nunca setar/ler o valor plano
    # diretamente, usar as properties login_plain / senha_plain abaixo.
    login_encrypted = models.TextField(blank=True)
    senha_encrypted = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Conexão de Operadora TISS'
        verbose_name_plural = 'Conexões de Operadora TISS'
        # É esta constraint (não a de TISSOperatorConfig) que impede
        # credencial duplicada por acidente: duas configs da mesma clínica
        # com o mesmo (endpoint, transporte) SEMPRE compartilham a linha
        # aqui — ver `get_or_create_for`, usado tanto pelo model quanto pela
        # migração de dados 0011.
        unique_together = [('clinic', 'endpoint_url', 'gateway_provider')]

    def __str__(self):
        # Nunca incluir login/senha aqui.
        return f'Conexão {self.gateway_provider} — {self.clinic.name} ({self.endpoint_url})'

    def set_login(self, plain: str):
        self.login_encrypted = encrypt_credential(plain)

    def set_senha(self, plain: str):
        self.senha_encrypted = encrypt_credential(plain)

    @property
    def login_plain(self) -> str:
        return decrypt_credential(self.login_encrypted)

    @property
    def senha_plain(self) -> str:
        return decrypt_credential(self.senha_encrypted)

    @classmethod
    def get_or_create_for(cls, *, clinic, gateway_provider, endpoint_url, login=None, senha=None):
        """
        Reaproveita a connection existente para (clinic, endpoint_url,
        gateway_provider) em vez de criar uma nova — é isto que consolida N
        `TISSOperatorConfig` do mesmo agregador numa única credencial. Usado
        tanto pelo caminho de criação normal (`TISSOperatorConfig.__init__`
        legado, serializer) quanto pela migração de dados 0011.
        """
        connection, _created = cls.objects.get_or_create(
            clinic=clinic,
            endpoint_url=endpoint_url or '',
            gateway_provider=gateway_provider or TISSGatewayProvider.DESCONHECIDO,
        )
        if login or senha:
            if login:
                connection.set_login(login)
            if senha:
                connection.set_senha(senha)
            connection.save()
        return connection


class TISSOperatorConfig(models.Model):
    """
    Particularidade de UMA operadora real (registro ANS) para uma clínica —
    transporte/endpoint/credencial NÃO moram mais aqui, moram em
    `TISSOperatorConnection` (ver docstring acima e §2 do documento de
    estratégia multi-operadora). Uma clínica pode ter mais de uma operadora
    configurada (uma por convênio) — nunca a mesma operadora aparece em
    lotes de clínicas diferentes (isolamento por FK obrigatória a Clinic).

    `clinic` continua replicado aqui (redundante com `connection.clinic`) de
    propósito: mantém `unique_together` e todo o código existente que já
    filtra/exibe por `operator_config.clinic` sem indireção, e o `clean()`
    abaixo garante que a redundância nunca diverge (invariante de
    isolamento multi-tenant — uma clínica nunca pode apontar para a
    connection de outra).

    Para compatibilidade com todo o código/testes que já criava configs
    passando `endpoint_url`/`gateway_provider`/`login`/`senha` diretamente
    (antes de existir `TISSOperatorConnection`), o construtor aceita esses
    parâmetros legados e materializa/reaproveita a connection por baixo —
    ver `__init__`. Código novo deve preferir passar `connection=` explícito.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='tiss_operator_configs')
    connection = models.ForeignKey(
        TISSOperatorConnection, on_delete=models.PROTECT, related_name='operator_configs',
        help_text='Transporte/endpoint/credencial compartilhados (ex.: a Orizon desta clínica).',
    )
    nome_operadora = models.CharField(max_length=70)
    registro_ans = models.CharField(max_length=6, help_text='Registro ANS da operadora (6 dígitos)')
    cnpj_operadora = models.CharField(max_length=14, blank=True)

    ativo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuração de Operadora TISS'
        verbose_name_plural = 'Configurações de Operadora TISS'
        unique_together = [('clinic', 'registro_ans')]
        indexes = [models.Index(fields=['clinic', 'ativo'])]

    def __init__(self, *args, **kwargs):
        connection = kwargs.pop('connection', None)
        endpoint_url = kwargs.pop('endpoint_url', None)
        gateway_provider = kwargs.pop('gateway_provider', None)
        login = kwargs.pop('login', None)
        senha = kwargs.pop('senha', None)
        super().__init__(*args, **kwargs)
        if connection is not None:
            self.connection = connection
        elif endpoint_url is not None or gateway_provider is not None or login is not None or senha is not None:
            # Caminho de compatibilidade retroativa (ver docstring da classe):
            # reaproveita/consolida a connection em vez de criar uma nova por
            # config — é exatamente a correção do defeito de credencial
            # duplicada, feita de graça para quem já chamava
            # `TISSOperatorConfig(endpoint_url=..., gateway_provider=...)`.
            self.connection = TISSOperatorConnection.get_or_create_for(
                clinic=self.clinic,
                gateway_provider=gateway_provider or TISSGatewayProvider.DESCONHECIDO,
                endpoint_url=endpoint_url or '',
                login=login, senha=senha,
            )

    def clean(self):
        if self.connection_id and self.clinic_id and self.connection.clinic_id != self.clinic_id:
            raise ValidationError('connection pertence a outra clínica — violação de isolamento multi-tenant')

    def __str__(self):
        # Nunca incluir login/senha aqui.
        return f'{self.nome_operadora} ({self.registro_ans}) — {self.clinic.name}'

    # ------------------------------------------------------------------
    # Proxies de compatibilidade para `connection` — usados por código e
    # testes escritos antes da separação. Código NOVO deve ler/escrever
    # `operator_config.connection.*` diretamente (mais explícito sobre quem
    # é dono do dado); estes proxies existem só para não quebrar chamadores
    # existentes (providers legados, admin, testes).
    # ------------------------------------------------------------------
    @property
    def endpoint_url(self) -> str:
        return self.connection.endpoint_url

    @property
    def gateway_provider(self) -> str:
        return self.connection.gateway_provider

    @property
    def integracao_automatica(self) -> bool:
        """
        BACFF-016: True somente quando a clínica pode contar com a chamada
        automática de fato disparando contra a operadora — hoje, só o client
        Orizon está implementado e homologado (ver `TISSGatewayProvider`
        acima). `ativo=False` também bloqueia a chamada de negócio
        (`services.py`), então uma config Orizon desativada não deve ostentar
        o selo de integração automática: ela cairia no fallback manual do
        mesmo jeito. Qualquer outro provider (inclusive `generico_ans` e
        `desconhecido`) é sempre confirmação manual — nenhuma mudança na
        lógica de despacho, só o que é EXPOSTO na API/admin.
        """
        return self.ativo and self.gateway_provider == TISSGatewayProvider.ORIZON

    @property
    def login_encrypted(self) -> str:
        return self.connection.login_encrypted

    @property
    def senha_encrypted(self) -> str:
        return self.connection.senha_encrypted

    def set_login(self, plain: str):
        self.connection.set_login(plain)
        self.connection.save(update_fields=['login_encrypted', 'updated_at'])

    def set_senha(self, plain: str):
        self.connection.set_senha(plain)
        self.connection.save(update_fields=['senha_encrypted', 'updated_at'])

    @property
    def login_plain(self) -> str:
        return self.connection.login_plain

    @property
    def senha_plain(self) -> str:
        return self.connection.senha_plain


class TISSLoteStatus(models.TextChoices):
    MONTANDO = 'montando', 'Montando'
    VALIDADO = 'validado', 'Validado (XSD ok)'
    ENVIANDO = 'enviando', 'Enviando'
    ENVIADO = 'enviado', 'Enviado (protocolo recebido)'
    ERRO_ENVIO = 'erro_envio', 'Erro no envio'
    PROCESSADO_ACEITO = 'processado_aceito', 'Processado — aceito integralmente'
    PROCESSADO_GLOSA = 'processado_glosa', 'Processado — com glosa'


class TISSLote(models.Model):
    """
    Lote de guias TISS de UMA clínica para UMA operadora (o padrão TISS
    proíbe misturar operadoras num mesmo lote). numeroLote é sequencial por
    (clínica, operadora) — ver `next_numero_lote`.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, related_name='tiss_lotes')
    operator_config = models.ForeignKey(TISSOperatorConfig, on_delete=models.PROTECT, related_name='lotes')
    numero_lote = models.PositiveIntegerField()
    # Competência no formato YYYY-MM, mesmo padrão de billing.Invoice
    competencia = models.CharField(max_length=7)
    status = models.CharField(max_length=20, choices=TISSLoteStatus, default=TISSLoteStatus.MONTANDO)

    xml_enviado = models.TextField(blank=True)
    xml_recebido = models.TextField(blank=True)
    protocolo = models.CharField(max_length=20, blank=True)
    hash_epilogo = models.CharField(max_length=32, blank=True)
    erro_mensagem = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    enviado_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Lote TISS'
        verbose_name_plural = 'Lotes TISS'
        unique_together = [('clinic', 'operator_config', 'numero_lote')]
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['clinic', 'competencia']),
            models.Index(fields=['clinic', 'status']),
        ]

    def __str__(self):
        return f'Lote {self.numero_lote} — {self.clinic.name} ({self.status})'

    @classmethod
    def next_numero_lote(cls, clinic, operator_config) -> int:
        """
        Sequencial por (clínica, operadora). Não é atômico sozinho —
        quem chama deve estar dentro de uma transaction.atomic() com
        select_for_update no maior registro existente para evitar corrida
        (ver tiss/services.py::criar_lote). Isolado por FK: nunca olha lotes
        de outra clínica.
        """
        last = (
            cls.objects.select_for_update()
            .filter(clinic=clinic, operator_config=operator_config)
            .order_by('-numero_lote')
            .first()
        )
        return (last.numero_lote + 1) if last else 1


def mascarar_numero_carteira(valor: str) -> str:
    """
    BACFF-AVULSA-02: dado permanece em texto pleno no banco (necessário
    para montar o XML TISS real) — só a exibição (admin e API REST) é
    mascarada. Compartilhado entre `TISSGuiaAdmin` e `TISSGuiaSerializer`
    para não duplicar a regra em dois lugares.
    """
    if not valor:
        return '—'
    return f'****{valor[-4:]}' if len(valor) > 4 else '****'


_CPF_RE = re.compile(r'\d{3}\.?\d{3}\.?\d{3}-?\d{2}')
_CARTEIRA_OU_CNS_RE = re.compile(r'\b\d{8,20}\b')
_ULTIMO_ERRO_MAX_LEN = 500


def sanitizar_erro_operadora(mensagem: str) -> str:
    """
    BACFF-014 (revisão de segurança, 2026-07-30) — `ultimo_erro` em
    `TISSCancelamentoPendente` guarda texto de erro que pode se originar de
    um `<sch:descricaoErro>` DEVOLVIDO PELA PRÓPRIA ORIZON (ver
    `providers/orizon.py::cancelar_guia`, ramo `SOAPFaultResult`) — texto
    livre de um sistema externo que não controlamos, exibido em texto puro
    no Django Admin (`TISSCancelamentoPendenteAdmin`, readonly). Diferente
    dos códigos internos (`soap_network_error`, `guia_nao_cancelada`, etc.),
    não há garantia contratual de que a operadora nunca inclua dado de
    beneficiário (CPF, nº carteirinha/CNS) numa mensagem de validação.

    Melhor esforço, não perfeito: mascara padrões reconhecíveis de CPF e
    sequências longas de dígitos (carteirinha/CNS) e limita o tamanho — não
    tenta detectar nomes de pessoa (não há como fazer isso de forma
    confiável com regex). Ainda assim reduz a superfície de PII estruturada
    (CPF/carteirinha) que hoje era persistida sem qualquer tratamento.
    """
    if not mensagem:
        return mensagem
    sanitizado = _CPF_RE.sub('[cpf-mascarado]', mensagem)
    sanitizado = _CARTEIRA_OU_CNS_RE.sub('[numero-mascarado]', sanitizado)
    if len(sanitizado) > _ULTIMO_ERRO_MAX_LEN:
        sanitizado = sanitizado[:_ULTIMO_ERRO_MAX_LEN] + '…[truncado]'
    return sanitizado


class TISSGuiaStatus(models.TextChoices):
    NAO_ENVIADA = 'nao_enviada', 'Não enviada'
    ENVIADA = 'enviada', 'Enviada'
    ACEITA = 'aceita', 'Aceita'
    GLOSADA = 'glosada', 'Glosada'
    PARCIAL = 'parcial', 'Aceita parcialmente'
    CANCELADA = 'cancelada', 'Cancelada'


class TISSGuia(models.Model):
    """
    Guia individual dentro de um lote. `appointment_id` é uma referência
    externa ao agendamento no Edge Gateway (app desktop, banco por clínica)
    — não é FK, o backoffice não tem (nem deve ter) acesso ao banco clínico
    completo; só guarda o UUID retornado pelo endpoint de exportação do Edge.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, related_name='tiss_guias')
    lote = models.ForeignKey(TISSLote, on_delete=models.CASCADE, related_name='guias', null=True, blank=True)

    appointment_id = models.CharField(max_length=64, help_text='ID do agendamento no Edge Gateway (não é FK)')
    tipo = models.CharField(max_length=30, default='sp-sadt')
    numero = models.CharField(max_length=20, help_text='numeroGuiaPrestador')
    competencia = models.CharField(max_length=7)
    status = models.CharField(max_length=20, choices=TISSGuiaStatus, default=TISSGuiaStatus.NAO_ENVIADA)

    # Dados do beneficiário mínimos para montar o XML — nada de nome/CPF em
    # campos de log; ficam só aqui, no banco (LGPD: dado de negócio, não de log).
    numero_carteira = models.CharField(max_length=20, blank=True)
    beneficiario_nome = models.CharField(max_length=70, blank=True)

    procedimentos = models.JSONField(default=list, blank=True)
    valor = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # BACFF-014 (achado 2, atualização 2026-07-29): CID/indicação clínica da
    # guia — obrigatório pelo manual Autorize 4.03.00 (Cap. 10) como
    # <sch:indicacaoClinica>, filho de solicitacaoSP-SADT. Ausente até então
    # em orizon_autorize_xml_builder.py, o que provavelmente rejeitava a
    # solicitação por schema inválido na Orizon.
    indicacao_clinica = models.CharField(
        max_length=500, blank=True,
        help_text='CID/indicação clínica da guia (usado no <indicacaoClinica> do Autorize Orizon)',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Guia TISS'
        verbose_name_plural = 'Guias TISS'
        indexes = [
            models.Index(fields=['clinic', 'competencia', 'status']),
            models.Index(fields=['clinic', 'appointment_id']),
        ]

    def __str__(self):
        return f'Guia {self.numero} — {self.clinic.name} ({self.status})'


class TISSCancelamentoPendente(models.Model):
    """
    BACFF-014 (gap "cancelamento de guia", 2026-07-30) — alerta operacional
    para quando o cancelamento automático de uma guia junto à operadora
    (`cancelar_guia`, disparado via Celery ao cancelar o atendimento) esgota
    as 3 tentativas de retry sem sucesso.

    Decisão de produto do usuário (2026-07-30): não pode falhar
    silenciosamente. Este model é a fila de trabalho manual do suporte —
    a guia continua "cancelada" do lado da clínica, mas a Orizon nunca foi
    avisada, então alguém precisa agir (reconciliar manualmente com a
    operadora ou reenfileirar). Nenhum model reaproveitável já existia no
    projeto para esse tipo de alerta acionável por guia (SystemHeartbeat/
    SystemLog, em `metrics/models.py`, são genéricos de saúde do gateway,
    não carregam a guia/tentativas/motivo de falha necessários aqui).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, related_name='tiss_cancelamentos_pendentes')
    guia = models.ForeignKey(TISSGuia, on_delete=models.CASCADE, related_name='cancelamentos_pendentes')

    tentativas = models.PositiveSmallIntegerField(default=0)
    falhou_apos_retries = models.BooleanField(
        default=False,
        help_text='True quando as 3 tentativas de retry do Celery se esgotaram sem sucesso — precisa de ação manual.',
    )
    # Técnica (código/motivo de falha). Pode incluir texto livre devolvido
    # pela operadora (`descricaoErro` de um SOAP fault) — sempre passar por
    # `sanitizar_erro_operadora` antes de gravar aqui (ver `tasks.py::
    # _tratar_falha`); não confiar apenas na convenção de não incluir PII,
    # a operadora é um sistema externo fora do nosso controle.
    ultimo_erro = models.TextField(blank=True)
    resolvido = models.BooleanField(default=False, help_text='Marcar quando o suporte reconciliar manualmente com a operadora.')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Cancelamento de Guia Pendente (alerta)'
        verbose_name_plural = 'Cancelamentos de Guia Pendentes (alertas)'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['clinic', 'falhou_apos_retries']),
            models.Index(fields=['clinic', 'resolvido']),
        ]

    def __str__(self):
        return f'Cancelamento pendente — guia {self.guia.numero} ({self.clinic.name})'


class TISSGlosa(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    guia = models.ForeignKey(TISSGuia, on_delete=models.CASCADE, related_name='glosas')
    codigo = models.CharField(max_length=10)
    descricao = models.CharField(max_length=500, blank=True)
    valor_glosado = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    recurso_enviado = models.BooleanField(default=False)
    resposta = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Glosa TISS'
        verbose_name_plural = 'Glosas TISS'
        indexes = [models.Index(fields=['codigo'])]

    def __str__(self):
        return f'Glosa {self.codigo} — guia {self.guia.numero}'

    def clean(self):
        # Uma guia só pode ter glosa de uma clínica coerente com a própria guia
        # (defesa em profundidade — não deveria nem ser possível construir isso
        # via API pois TISSGlosa não tem FK direta a Clinic).
        if self.valor_glosado < 0:
            raise ValidationError('valor_glosado não pode ser negativo')


class TISSElegibilidadeOrigem(models.TextChoices):
    """
    BACFF-013: mesmo padrão já usado em GuiaTISS.Origem no gateway Go
    (OrigemGuiaAutomatica/OrigemGuiaManual) — a consulta pode ter sido
    decidida pela integração SOAP automática ou registrada manualmente pela
    recepção (quando a operadora liga/portal é acessado diretamente).
    """
    AUTOMATICA = 'automatica', 'Automática (SOAP)'
    MANUAL = 'manual', 'Manual (recepção)'


class TISSElegibilidadeStatus(models.TextChoices):
    SUCESSO = 'sucesso', 'Sucesso (resposta recebida da operadora)'
    FALHA_TRANSPORTE = 'falha_transporte', 'Falha de transporte (SOAP)'
    FALHA_OPERADORA = 'falha_operadora', 'Operadora rejeitou a própria consulta'
    # BO-08.5: autorização recebida como "Em Análise" (Orizon Autorize,
    # situacaoAutorizacao=2) — não é falha nenhuma, é um estado transitório
    # legítimo que só a consulta de status assíncrona resolve depois (ver
    # `TISSAutorizacaoPendente`). Distinto de SUCESSO para que `services`
    # possa decidir, de forma genérica (sem `if operadora == 'orizon'`),
    # quando vale a pena registrar uma pendência de acompanhamento.
    EM_ANALISE = 'em_analise', 'Em análise (aguardando confirmação da operadora)'


class TISSElegibilidadeConsulta(models.Model):
    """
    LOG OPERACIONAL de uma consulta de elegibilidade — NÃO é mais um
    registro de auditoria com o conteúdo clínico completo (BACFF-AVULSA-01,
    achado de Security Engineer em 2026-07-16: o conteúdo antigo —
    numero_carteira, beneficiario_nome, elegivel, motivos_negativa, XML —
    ficava no banco CENTRAL multi-tenant, visível a qualquer suporte com
    acesso à clínica. O admin do sistema não tem motivo de negócio para
    saber SE UM PACIENTE ESPECÍFICO é elegível; só precisa saber se a
    integração com a operadora funcionou, para agir rápido em caso de
    falha). O resultado completo (elegível, motivos, nome do beneficiário)
    continua sendo devolvido na resposta HTTP síncrona ao Edge Gateway —
    só deixa de ser PERSISTIDO aqui. A cópia completa e duradoura passa a
    viver no banco LOCAL da clínica (SQLite do gateway), nunca neste banco.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, related_name='tiss_elegibilidade_consultas')
    operator_config = models.ForeignKey(
        TISSOperatorConfig, on_delete=models.PROTECT, related_name='elegibilidade_consultas',
    )
    appointment_id = models.CharField(
        max_length=64, blank=True,
        help_text='ID do agendamento no Edge Gateway (não é FK, mesmo padrão de TISSGuia.appointment_id) — referência opaca, não é PII por si só',
    )

    origem = models.CharField(max_length=10, choices=TISSElegibilidadeOrigem, default=TISSElegibilidadeOrigem.AUTOMATICA)
    status = models.CharField(max_length=20, choices=TISSElegibilidadeStatus)
    # Mensagem TÉCNICA (ex.: "falha_soap: timeout", "SchemaInvalido") — nunca
    # deve conter nome/carteirinha do beneficiário. Quem monta essa mensagem
    # em services.py é responsável por essa garantia.
    erro_mensagem = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Log de Consulta de Elegibilidade TISS'
        verbose_name_plural = 'Logs de Consulta de Elegibilidade TISS'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['clinic', 'created_at']),
            models.Index(fields=['clinic', 'status']),
        ]

    def __str__(self):
        return f'Elegibilidade — {self.clinic.name} ({self.origem}, {self.status})'


class TISSAutorizacaoSituacao(models.TextChoices):
    """
    Mesmo vocabulário de `orizon_autorize_client.SituacaoAutorizacao` (os
    valores são iguais de propósito — evita mapeamento redundante entre o
    client e este model). `EM_ANALISE` é o estado inicial/transitório;
    `AUTORIZADO`/`NEGADO` são terminais.
    """
    EM_ANALISE = 'em_analise', 'Em análise (aguardando operadora)'
    AUTORIZADO = 'autorizado', 'Autorizado'
    NEGADO = 'negado', 'Negado'


class TISSAutorizacaoPendente(models.Model):
    """
    BO-08.5 — fila de acompanhamento de autorizações que a operadora
    respondeu como "Em Análise" (`solicitacaoProcedimentoWS` ->
    `situacaoAutorizacao=2`, ver `providers/orizon.py::verificar_cobertura`).

    Diferente de `TISSElegibilidadeConsulta` (log append-only, nunca lido de
    volta pelo próprio sistema): este é um registro de TRABALHO, mutável,
    que a task periódica `tiss/tasks.py::consultar_autorizacoes_pendentes_task`
    consulta e atualiza até a operadora responder em definitivo
    (`tissSolicitacaoStatusAutorizacao_Operation`). Só entra aqui quando o
    provider consegue identificar `numero_guia_prestador` (ver
    `services._registrar_autorizacao_pendente` — sem esse identificador não
    há como consultar o status depois, então a pendência não é registrada e
    fica só no log operacional).

    LGPD: mesmos limites de `OperatorCallLog`/`TISSCancelamentoPendente` —
    nenhum nome de beneficiário, nenhuma carteirinha, nenhum XML. Só
    identificadores de guia (não-PII por si só) e metadado de negócio.
    `descricao_glosa`/`ultimo_erro_consulta` passam por
    `sanitizar_erro_operadora` antes de persistir (mesmo texto livre vindo
    da operadora que motivou o tratamento em `TISSCancelamentoPendente`).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, related_name='tiss_autorizacoes_pendentes')
    operator_config = models.ForeignKey(
        TISSOperatorConfig, on_delete=models.PROTECT, related_name='autorizacoes_pendentes',
    )
    appointment_id = models.CharField(
        max_length=64, blank=True,
        help_text='ID do agendamento no Edge Gateway (não é FK) — referência opaca, pode estar vazio para consultas avulsas',
    )
    numero_guia_prestador = models.CharField(
        max_length=20, help_text='numeroGuiaPrestador — chave usada para consultar o status depois',
    )
    numero_guia_operadora = models.CharField(max_length=20, blank=True)

    situacao = models.CharField(
        max_length=20, choices=TISSAutorizacaoSituacao, default=TISSAutorizacaoSituacao.EM_ANALISE,
    )
    codigo_glosa = models.CharField(max_length=10, blank=True)
    descricao_glosa = models.CharField(max_length=500, blank=True)

    tentativas_consulta = models.PositiveIntegerField(default=0)
    ultima_consulta_em = models.DateTimeField(null=True, blank=True)
    ultimo_erro_consulta = models.TextField(blank=True)

    # Terminal (AUTORIZADO/NEGADO): a task periódica para de consultar. Campo
    # próprio (em vez de derivar de `situacao != EM_ANALISE`) porque é o que
    # a query da task filtra (`resolvido=False`) — filtrar por exclusão de
    # enum acopla a query à lista de estados não-terminais, que cresce mais
    # fácil de errar do que este booleano explícito.
    resolvido = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Autorização TISS Pendente'
        verbose_name_plural = 'Autorizações TISS Pendentes'
        ordering = ['-created_at']
        # Idempotência (requisito BO-08.5): a mesma guia "em análise" nunca
        # gera duas linhas de pendência, mesmo se `services.consultar_
        # elegibilidade_automatica` for chamado de novo antes da operadora
        # responder (retry do gateway, nova tentativa do usuário etc.).
        unique_together = [('clinic', 'operator_config', 'numero_guia_prestador')]
        indexes = [
            models.Index(fields=['resolvido']),
            models.Index(fields=['clinic', 'situacao']),
        ]

    def __str__(self):
        return f'Autorização pendente {self.numero_guia_prestador} — {self.clinic.name} ({self.situacao})'


class OperatorCallOperation(models.TextChoices):
    COBERTURA = 'cobertura', 'Cobertura (elegibilidade/autorização)'
    ENVIO_LOTE = 'envio_lote', 'Envio de lote'
    CANCELAMENTO = 'cancelamento', 'Cancelamento de guia'
    CONSULTA_STATUS = 'consulta_status', 'Consulta de status de autorização'


class OperatorCallOutcome(models.TextChoices):
    SUCCESS = 'success', 'Sucesso'
    SOAP_FAULT = 'soap_fault', 'Operadora rejeitou a requisição'
    NETWORK_ERROR = 'network_error', 'Falha de rede/transporte'
    PROVIDER_ERROR = 'provider_error', 'Erro estrutural de provider'


class OperatorCallLog(models.Model):
    """
    §4.4(a) do documento de arquitetura + ADMIN-DASHBOARD-REDESIGN §4.1.
    Append-only, uma linha por chamada de negócio a uma operadora.
    Responde "a operadora X está saudável?" de forma uniforme para QUALQUER
    provider plugado.

    A chave de agregação é `registro_ans` (chave de negócio genérica), NÃO o
    nome do gateway — o dashboard nunca precisa de um `if orizon`.

    **Onde a escrita acontece, e por quê:** não dentro de `soap_client.py`
    (isso obrigaria cada provider novo a lembrar de instrumentar), e sim em
    `tiss/providers/__init__.py`, envolvendo a chamada ao provider já
    resolvido. Assim um provider novo ganha observabilidade de graça, sem
    uma linha de instrumentação própria — é o que torna o health check
    genérico de verdade em vez de "genérico se o dev lembrar".

    **LGPD — o que esta tabela deliberadamente NÃO tem:** nenhum payload,
    nenhum XML, nenhum `erro_mensagem` cru, nenhum `numero_carteira`,
    nenhum nome de beneficiário. A resposta TISS contém PHI; só metadado
    operacional entra aqui. `outcome` é um enum fechado justamente para que
    não haja campo de texto livre onde alguém possa despejar a resposta da
    operadora "só para depurar".

    Retenção: purgar > 90 dias (`manage.py purgar_operator_call_log`),
    senão a tabela cresce sem limite e vira custo de banco.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    registro_ans = models.CharField(max_length=6, help_text='Chave de agregação genérica — nunca o nome do gateway')
    gateway_provider = models.CharField(max_length=20, choices=TISSGatewayProvider)
    operation = models.CharField(max_length=20, choices=OperatorCallOperation)
    clinic = models.ForeignKey(Clinic, on_delete=models.SET_NULL, null=True, blank=True, related_name='tiss_operator_calls')
    outcome = models.CharField(max_length=20, choices=OperatorCallOutcome)
    latency_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'Log de Chamada à Operadora'
        verbose_name_plural = 'Logs de Chamada à Operadora'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['registro_ans', 'created_at']),
            models.Index(fields=['clinic', 'created_at']),
        ]

    def __str__(self):
        return f'{self.registro_ans} — {self.operation} ({self.outcome}, {self.latency_ms}ms)'


class TUSSProcedureCode(models.Model):
    """
    Tabela mestre de códigos TUSS — fonte de verdade única (EDGW-013/BACFF).
    O Edge Gateway consulta aqui sob demanda (cache-aside) e persiste uma
    cópia local no SQLite da clínica; buscas seguintes batem só no local
    até uma nova entrada aparecer aqui. Dado regulatório público, não é
    dado de clínica — sem FK a Clinic.
    """
    tuss_code = models.CharField(max_length=10, primary_key=True)
    # 500, não 255: a tabela oficial TUSS 22 da ANS tem descrições de até
    # 302 caracteres (achado real ao rodar migrate contra Postgres real pela
    # primeira vez, 2026-08-10 — 255 quebrava o seed com DataError em 5
    # códigos). Ver migration 0005_seed_tuss22_completo.
    description = models.CharField(max_length=500)
    table_code = models.CharField(max_length=2, default='22', help_text="'22'=médico, '90'=odontológico")
    updated_at = models.DateTimeField(auto_now=True, help_text='Usado pelo gateway para invalidar cache local quando a ANS atualizar este registro')

    class Meta:
        verbose_name = 'Código TUSS (referência)'
        verbose_name_plural = 'Códigos TUSS (referência)'
        indexes = [models.Index(fields=['table_code'])]

    def __str__(self):
        return f'{self.tuss_code} — {self.description}'


class TISSDocumentoAssinaturaStatus(models.TextChoices):
    """
    TASK-BO-10 — ciclo de vida do envio assíncrono de `envioDocumentoWS`
    (única operação Orizon que exige assinatura XMLDSig; ver
    `tiss/orizon_envio_documento_xml_builder.py`).
    """
    PENDENTE_ASSINATURA = 'pendente_assinatura', 'Pendente de assinatura (aguardando gateway)'
    ASSINADO = 'assinado', 'Assinado (bloco recebido, pronto para transmitir)'
    ENVIADO = 'enviado', 'Enviado (protocolo recebido da operadora)'
    ERRO_ENVIO = 'erro_envio', 'Erro no envio'


class TISSDocumentoAssinatura(models.Model):
    """
    TASK-BO-10 — fila de assinatura XMLDSig para `envioDocumentoWS` (Orizon).
    O certificado .p12 (A1, ICP-Brasil) NUNCA sai do gateway local da clínica
    (decisão de arquitetura fechada) — este registro só coordena o handoff:

    1. Backoffice monta o fragmento e canonicaliza (C14N) até o ponto da
       assinatura, SEM o bloco <Signature> — `fragmento_canonico` guarda esse
       resultado como STRING/bytes já formatados (produzido uma única vez por
       `orizon_envio_documento_xml_builder.build_envio_documento_fragment`).
       Status nasce em PENDENTE_ASSINATURA.
    2. Gateway (SyncWorker, lado Go — EDGW-073, fora desta task) puxa esse
       fragmento via GET de sync (`tiss/views.py::sync_documentos_pendentes`),
       assina localmente e devolve, no push seguinte, só o bloco de assinatura
       (SignedInfo/SignatureValue/KeyInfo) via POST de sync
       (`sync_documentos_assinatura`).
    3. `aplicar_bloco_assinatura` reinsere esse bloco em `fragmento_canonico`
       por CONCATENAÇÃO/INSERÇÃO TEXTUAL — nunca reparseia nem re-serializa o
       fragmento com uma lib de XML. Isso é crítico: C14N pode produzir bytes
       diferentes numa segunda passada (ordenação de atributos, whitespace),
       o que invalidaria a assinatura. `xml_final` é o resultado, e o teste
       `tests_xmldsig_c14n_integridade.py` prova que os bytes de
       `fragmento_canonico` permanecem idênticos dentro de `xml_final` depois
       dessa inserção (critério de aceite formal da task).
    4. Transmitido via `tiss/soap_client.py::enviar_documento` (mesmo client
       de BO-08/BO-09, sem client SOAP novo).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, related_name='tiss_documentos_assinatura')
    guia = models.ForeignKey(TISSGuia, on_delete=models.PROTECT, related_name='documentos_assinatura')
    operator_config = models.ForeignKey(
        TISSOperatorConfig, on_delete=models.PROTECT, related_name='documentos_assinatura',
    )

    status = models.CharField(
        max_length=20, choices=TISSDocumentoAssinaturaStatus,
        default=TISSDocumentoAssinaturaStatus.PENDENTE_ASSINATURA,
    )

    # Fragmento canônico (C14N) SEM <Signature> — string exata que o gateway
    # deve assinar. Nunca reparsear/re-serializar depois de gravado.
    fragmento_canonico = models.TextField()
    root_tag = models.CharField(
        max_length=100,
        help_text='Nome qualificado da tag raiz (ex.: sch:envioDocumentoWS) — usado só para achar o ponto de inserção textual do bloco de assinatura, nunca para reparsear o fragmento.',
    )
    sequencial_transacao = models.CharField(max_length=20, blank=True)

    # Bloco <Signature>...</Signature> devolvido pelo gateway (texto puro,
    # nunca contém o certificado nem a chave privada — só SignedInfo/
    # SignatureValue/KeyInfo).
    signature_block = models.TextField(blank=True)

    # fragmento_canonico com signature_block inserido por texto — é isto que
    # de fato vai para soap_client.enviar_documento. Nunca é o resultado de
    # reparsear+serializar fragmento_canonico.
    xml_final = models.TextField(blank=True)

    protocolo = models.CharField(max_length=20, blank=True)
    erro_mensagem = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    assinado_at = models.DateTimeField(null=True, blank=True)
    enviado_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Documento TISS pendente de assinatura (XMLDSig)'
        verbose_name_plural = 'Documentos TISS pendentes de assinatura (XMLDSig)'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['clinic', 'status']),
        ]

    def __str__(self):
        return f'Documento {self.guia.numero} — {self.clinic.name} ({self.status})'

    def clean(self):
        if self.clinic_id and self.guia_id and self.guia.clinic_id != self.clinic_id:
            raise ValidationError('guia pertence a outra clínica — violação de isolamento multi-tenant')
        if self.clinic_id and self.operator_config_id and self.operator_config.clinic_id != self.clinic_id:
            raise ValidationError('operator_config pertence a outra clínica — violação de isolamento multi-tenant')

    def aplicar_bloco_assinatura(self, signature_block: str) -> None:
        """
        Reinsere `signature_block` (recebido do gateway) dentro de
        `fragmento_canonico` por INSERÇÃO TEXTUAL — nunca via etree.parse +
        etree.tostring, que invalidaria a assinatura (C14N não é
        necessariamente idempotente byte-a-byte entre duas serializações).

        Insere imediatamente antes da tag de fechamento da raiz
        (`</{root_tag}>`), que é a única ocorrência exata dessa string no
        documento (fragmento sem <Signature> é bem-formado e a raiz só tem
        uma tag de fechamento com esse nome completo).
        """
        if self.status != TISSDocumentoAssinaturaStatus.PENDENTE_ASSINATURA:
            raise ValidationError(f'documento não está pendente de assinatura (status={self.status})')
        if not signature_block or '<Signature' not in signature_block:
            raise ValidationError('signature_block_invalido')

        closing_tag = f'</{self.root_tag}>'
        if self.fragmento_canonico.count(closing_tag) != 1:
            raise ValidationError('fragmento_canonico_sem_ponto_de_insercao_unico')

        xml_final = self.fragmento_canonico.replace(
            closing_tag, f'{signature_block}{closing_tag}', 1,
        )

        self.signature_block = signature_block
        self.xml_final = xml_final
        self.status = TISSDocumentoAssinaturaStatus.ASSINADO
        self.assinado_at = timezone.now()
        self.save(update_fields=[
            'signature_block', 'xml_final', 'status', 'assinado_at', 'updated_at',
        ])


class ANSInsuranceOperator(models.Model):
    """
    Tabela mestre de operadoras (registro ANS) — mesmo papel de
    TUSSProcedureCode, ver docstring acima. Não confundir com
    TISSOperatorConfig (que é a credencial/config DE UMA CLÍNICA para uma
    operadora já cadastrada aqui).
    """
    ans_code = models.CharField(max_length=10, primary_key=True)
    name = models.CharField(max_length=100)
    cnpj = models.CharField(max_length=14, blank=True)
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True, help_text='Usado pelo gateway para invalidar cache local quando a ANS atualizar este registro')

    class Meta:
        verbose_name = 'Operadora ANS (referência)'
        verbose_name_plural = 'Operadoras ANS (referência)'

    def __str__(self):
        return f'{self.name} ({self.ans_code})'
