from django.db import models


class SystemHeartbeat(models.Model):
    clinic = models.OneToOneField(
        'clinics.Clinic',
        on_delete=models.CASCADE,
        related_name='heartbeat',
    )
    gateway_version = models.CharField(max_length=20)
    os_info = models.CharField(max_length=100, blank=True)
    db_size_mb = models.FloatField(default=0)
    pending_sync = models.IntegerField(default=0)
    sync_connected = models.BooleanField(default=False)
    last_seen = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-last_seen']
        verbose_name = 'Heartbeat'
        get_latest_by = 'last_seen'

    def __str__(self):
        return f'{self.clinic.name} — {self.last_seen:%Y-%m-%d %H:%M}'


class LogLevel(models.TextChoices):
    INFO = 'info', 'Info'
    WARN = 'warn', 'Aviso'
    ERROR = 'error', 'Erro'


class SystemLog(models.Model):
    clinic = models.ForeignKey(
        'clinics.Clinic',
        on_delete=models.CASCADE,
        related_name='system_logs',
    )
    level = models.CharField(max_length=10, choices=LogLevel)
    message = models.TextField()
    # BACFF-005 (LGPD): context não deve conter dados pessoais/PHI — apenas
    # metadados técnicos (module, duration_ms, error_code, etc). Ver
    # metrics.views.sanitize_log_context, aplicado antes do bulk_create.
    context = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-occurred_at']
        verbose_name = 'Log do Sistema'
        verbose_name_plural = 'Logs do Sistema'

    def __str__(self):
        return f'[{self.level}] {self.clinic.name} — {self.message[:60]}'
