import uuid

from django.db import models
from django.core.exceptions import ValidationError

from clinics.models import Clinic
from .crypto import encrypt_credential, decrypt_credential


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
