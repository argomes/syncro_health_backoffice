from django.contrib.auth.models import AbstractUser
from django.db import models
from django.core.exceptions import ValidationError


class SupportUser(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        SUPPORT = 'support', 'Suporte'
        BILLING = 'billing', 'Faturamento'

    role = models.CharField(max_length=20, choices=Role, default=Role.SUPPORT)

    class Meta:
        verbose_name = 'Usuário de Suporte'
        verbose_name_plural = 'Usuários de Suporte'

    def __str__(self):
        return f'{self.username} ({self.role})'


class ClinicAccess(models.Model):
    """
    Controla quais clínicas cada admin/support user pode acessar.

    Exemplo:
    - João (ADMIN) tem acesso a [Clínica SP, Clínica RJ] com role OWNER
    - Maria (SUPPORT) tem acesso a [Clínica SP] com role VIEWER

    Regras:
    - OWNER: pode ver tudo, editar, deletar, gerenciar outros admins
    - ADMIN: pode ver tudo, editar
    - VIEWER: pode ver dados da clínica (read-only)
    """

    class AccessRole(models.TextChoices):
        OWNER = 'owner', 'Proprietário'
        ADMIN = 'admin', 'Administrador'
        VIEWER = 'viewer', 'Visualizador'

    support_user = models.ForeignKey(
        SupportUser,
        on_delete=models.CASCADE,
        related_name='clinic_accesses',
        help_text='Usuário que tem acesso'
    )
    clinic = models.ForeignKey(
        'clinics.Clinic',
        on_delete=models.CASCADE,
        related_name='admin_accesses',
        help_text='Clínica que pode acessar'
    )
    role = models.CharField(
        max_length=20,
        choices=AccessRole,
        default=AccessRole.VIEWER,
        help_text='Nível de permissão (owner/admin/viewer)'
    )
    granted_at = models.DateTimeField(auto_now_add=True)
    granted_by = models.ForeignKey(
        SupportUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name='clinic_accesses_granted',
        help_text='Quem concedeu acesso (para auditoria)'
    )
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('support_user', 'clinic')
        ordering = ['-granted_at']
        verbose_name = 'Acesso de Clínica'
        verbose_name_plural = 'Acessos de Clínica'
        indexes = [
            models.Index(fields=['support_user', 'role']),
            models.Index(fields=['clinic', 'role']),
        ]

    def __str__(self):
        return f'{self.support_user.username} → {self.clinic.name} ({self.role})'

    def clean(self):
        if self.revoked_at and self.role != self.AccessRole.VIEWER:
            raise ValidationError("Revokeados devem ter role VIEWER (leitura histórica)")

    def is_active(self):
        """Retorna True se acesso está ativo (não revogado)."""
        return self.revoked_at is None

    def revoke(self):
        """Marca acesso como revogado."""
        from django.utils import timezone
        self.revoked_at = timezone.now()
