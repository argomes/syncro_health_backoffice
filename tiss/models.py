import uuid

from django.db import models
from django.core.exceptions import ValidationError

from clinics.models import Clinic
from .crypto import encrypt_credential, decrypt_credential


class TISSGatewayProvider(models.TextChoices):
    """
    BACFF-014: qual client SOAP usar para esta operadora — o genérico
    (padrão ANS publicado, `tiss/soap_client.py`) ou um hub específico já
    integrado (`tiss/orizon_autorize_client.py`). Novas operadoras/hubs
    especulativos (Sulamérica, Porto Seguro etc.) NÃO entram aqui até
    terem client próprio implementado.
    """
    GENERICO_ANS = 'generico_ans', 'Genérico (padrão ANS)'
    ORIZON = 'orizon', 'Orizon (Autorize)'


class TISSOperatorConfig(models.Model):
    """
    Credenciais/endpoint de uma operadora (ou hub, ex: Orizon) para uma
    clínica específica. Uma clínica pode ter mais de uma operadora
    configurada (uma por convênio) — nunca a mesma operadora aparece em
    lotes de clínicas diferentes (isolamento por FK obrigatória a Clinic).

    login/senha NUNCA ficam em texto plano no banco — só o token Fernet.
    Não expor em __str__, admin list_display, serializers ou logs.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='tiss_operator_configs')
    nome_operadora = models.CharField(max_length=70)
    registro_ans = models.CharField(max_length=6, help_text='Registro ANS da operadora (6 dígitos)')
    cnpj_operadora = models.CharField(max_length=14, blank=True)
    endpoint_url = models.URLField(max_length=255)
    gateway_provider = models.CharField(
        max_length=20, choices=TISSGatewayProvider, default=TISSGatewayProvider.GENERICO_ANS,
        help_text='Qual client SOAP usar para esta operadora (genérico ANS ou hub específico como Orizon)',
    )

    # Sempre armazenados cifrados (Fernet) — nunca setar/ler o valor plano
    # diretamente, usar as properties login_plain / senha_plain abaixo.
    login_encrypted = models.TextField(blank=True)
    senha_encrypted = models.TextField(blank=True)

    ativo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuração de Operadora TISS'
        verbose_name_plural = 'Configurações de Operadora TISS'
        unique_together = [('clinic', 'registro_ans')]
        indexes = [models.Index(fields=['clinic', 'ativo'])]

    def __str__(self):
        # Nunca incluir login/senha aqui.
        return f'{self.nome_operadora} ({self.registro_ans}) — {self.clinic.name}'

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


class TISSGuiaStatus(models.TextChoices):
    NAO_ENVIADA = 'nao_enviada', 'Não enviada'
    ENVIADA = 'enviada', 'Enviada'
    ACEITA = 'aceita', 'Aceita'
    GLOSADA = 'glosada', 'Glosada'
    PARCIAL = 'parcial', 'Aceita parcialmente'


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


class TUSSProcedureCode(models.Model):
    """
    Tabela mestre de códigos TUSS — fonte de verdade única (EDGW-013/BACFF).
    O Edge Gateway consulta aqui sob demanda (cache-aside) e persiste uma
    cópia local no SQLite da clínica; buscas seguintes batem só no local
    até uma nova entrada aparecer aqui. Dado regulatório público, não é
    dado de clínica — sem FK a Clinic.
    """
    tuss_code = models.CharField(max_length=10, primary_key=True)
    description = models.CharField(max_length=255)
    table_code = models.CharField(max_length=2, default='22', help_text="'22'=médico, '90'=odontológico")
    updated_at = models.DateTimeField(auto_now=True, help_text='Usado pelo gateway para invalidar cache local quando a ANS atualizar este registro')

    class Meta:
        verbose_name = 'Código TUSS (referência)'
        verbose_name_plural = 'Códigos TUSS (referência)'
        indexes = [models.Index(fields=['table_code'])]

    def __str__(self):
        return f'{self.tuss_code} — {self.description}'


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
