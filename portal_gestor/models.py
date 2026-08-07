import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone

# Entidades que o gateway sabe ressincronizar sob demanda (ver TASK-041, lado
# syncro_gateway — internal/adapters/input/workers/health_worker.go).
SUPPORTED_RESYNC_ENTITIES = ('patients', 'appointments')

DEFAULT_REPORT_SESSION_TTL_HOURS = getattr(settings, 'REPORT_SESSION_TTL_HOURS', 2)


class ReportSessionStatus(models.TextChoices):
    PENDING = 'pending', 'Pendente'
    KEY_DELIVERED = 'key_delivered', 'Chave entregue ao gateway'
    SYNCING = 'syncing', 'Sincronizando'
    READY = 'ready', 'Pronto'
    EXPIRED = 'expired', 'Expirado'


class ReportSession(models.Model):
    """
    Sessão de relatório: representa o pedido de um ClinicUser para gerar um
    relatório sobre uma janela de datas, autorizado por uma TemporaryKey de
    curta duração (TASK-039/040/041, lado gateway).

    A TemporaryKey em si NUNCA é persistida em disco em texto claro — vive só
    no cache (Redis via django.core.cache, TTL = expires_at) até a entrega ser
    confirmada. Ver portal_gestor/services.py.

    Fluxo de status: pending → key_delivered → syncing → ready (ou → expired
    a qualquer momento). A transição para ready depende de um sinal do gateway
    confirmando que o resync foi persistido — chega no corpo do heartbeat como
    resync_ack (TASK-052, ver metrics/views.py::_apply_resync_ack).
    """

    session_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(
        'clinics.Clinic',
        on_delete=models.CASCADE,
        related_name='report_sessions',
    )
    created_by = models.ForeignKey(
        'accounts.ClinicUser',
        on_delete=models.SET_NULL,
        null=True,
        related_name='report_sessions_created',
    )

    # Escopo do relatório — entidades e janela de datas pedidas.
    entities_scope = models.JSONField(default=list, blank=True)
    date_from = models.DateTimeField()
    date_to = models.DateTimeField()

    status = models.CharField(
        max_length=20,
        choices=ReportSessionStatus,
        default=ReportSessionStatus.PENDING,
    )
    expires_at = models.DateTimeField()
    delivered_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Sessão de Relatório'
        verbose_name_plural = 'Sessões de Relatório'
        indexes = [
            models.Index(fields=['clinic', 'status']),
        ]

    def __str__(self):
        return f'{self.clinic.name} — {self.session_id} ({self.status})'

    def is_expired(self):
        return timezone.now() >= self.expires_at

    def mark_key_delivered(self):
        self.status = ReportSessionStatus.KEY_DELIVERED
        self.delivered_at = timezone.now()
        self.save(update_fields=['status', 'delivered_at', 'updated_at'])

    def mark_expired(self):
        self.status = ReportSessionStatus.EXPIRED
        self.save(update_fields=['status', 'updated_at'])

    def mark_ready(self):
        """
        Chamado quando o gateway confirma (via resync_ack no heartbeat, TASK-052) que
        terminou de verdade a ressincronização da janela pedida — fecha o TODO deixado
        pela TASK-042/043 (a sessão nunca chegava a `ready` antes disso).
        """
        self.status = ReportSessionStatus.READY
        self.save(update_fields=['status', 'updated_at'])


class PortalReadAuditLog(models.Model):
    """
    BACFF-AVULSA-05 (LGPD Art. 37 — registro das operações de tratamento de
    dados pessoais). Grava toda leitura BEM-SUCEDIDA de relatório feita via
    `_ReportReadView` (portal_gestor/views.py) — inclusive quando o resultado
    tem 0 registros, já que o acesso em si (a consulta autorizada pela
    TemporaryKey da sessão) já é a operação de tratamento a registrar, não só
    o retorno de dado.

    Guarda SOMENTE metadados da operação — NUNCA nome, documento, email,
    metadata clínica ou qualquer outro campo de PHI/PII do titular. Isso é
    estrutural, não uma promessa de código: o model só tem colunas de
    metadados (quem, qual clínica, qual sessão, qual entidade, quantos
    registros, quando) — não há campo aqui capaz de carregar dado do titular.

    Distinto do `audit_log` que já existe no gateway local (Go) — aquele
    audita ações já sincronizadas dentro da clínica; este audita o acesso de
    LEITURA feito remotamente, pelo próprio portal, sobre dado já sincronizado
    na nuvem.
    """

    clinic_user = models.ForeignKey(
        'accounts.ClinicUser',
        on_delete=models.SET_NULL,
        null=True,
        related_name='portal_read_audit_logs',
        help_text='Nulo se o usuário for removido depois — o registro de auditoria não pode desaparecer com ele.',
    )
    clinic = models.ForeignKey(
        'clinics.Clinic',
        on_delete=models.CASCADE,
        related_name='portal_read_audit_logs',
    )
    session_id = models.UUIDField(
        help_text='session_id da ReportSession que autorizou a leitura (não é FK — a sessão pode expirar/ser limpa sem apagar o log de auditoria).',
    )
    entity = models.CharField(
        max_length=32,
        help_text="Entidade acessada: 'patients', 'appointments', 'professionals' ou 'medical_records'.",
    )
    record_count = models.PositiveIntegerField(
        help_text='Quantidade de registros retornados pela leitura — pode ser 0.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Log de Auditoria de Leitura'
        verbose_name_plural = 'Logs de Auditoria de Leitura'
        indexes = [
            models.Index(fields=['clinic', 'created_at']),
            models.Index(fields=['session_id']),
        ]

    def __str__(self):
        return f'{self.clinic_id} leu {self.entity} ({self.record_count} registros) em {self.created_at}'


class ProductNotice(models.Model):
    """
    Aviso contextual dispensável (TASK-051) — substitui a ideia original de
    "mural de avisos genérico", rejeitada pela PO Healthtech em 2026-07-13
    (público de baixa frequência de acesso; mural fixo vira "vitrine ignorada"
    em poucos acessos). Conteúdo é o mesmo para todas as clínicas (não tem FK
    de clinic) — só a dispensa é por ClinicUser (ver ClinicUserNoticeDismissal).

    Só um aviso deveria estar `active=True` com vigência corrente por vez no
    MVP — não há fila/prioridade entre avisos concorrentes.
    """

    title = models.CharField(max_length=255)
    body = models.TextField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    active = models.BooleanField(default=True, help_text='Kill-switch manual — desativa sem precisar apagar.')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-starts_at']
        verbose_name = 'Aviso do Produto'
        verbose_name_plural = 'Avisos do Produto'

    def __str__(self):
        return self.title

    def is_current(self):
        now = timezone.now()
        return self.active and self.starts_at <= now <= self.ends_at


class ClinicUserNoticeDismissal(models.Model):
    """Registra que um ClinicUser específico já dispensou um ProductNotice —
    nunca mais aparece para ele, mesmo em nova sessão de login (por isso é uma
    tabela, não um cookie/localStorage, que não sobreviveria a outro dispositivo)."""

    clinic_user = models.ForeignKey(
        'accounts.ClinicUser',
        on_delete=models.CASCADE,
        related_name='notice_dismissals',
    )
    notice = models.ForeignKey(
        ProductNotice,
        on_delete=models.CASCADE,
        related_name='dismissals',
    )
    dismissed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('clinic_user', 'notice')
        verbose_name = 'Dispensa de Aviso'
        verbose_name_plural = 'Dispensas de Aviso'

    def __str__(self):
        return f'{self.clinic_user.email} dispensou "{self.notice.title}"'
