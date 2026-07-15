import uuid
from django.contrib.auth.hashers import make_password, check_password, is_password_usable
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


class ClinicUser(models.Model):
    """
    Usuário final da clínica (admin/gerente) que loga no portal_gestor —
    diferente de SupportUser, que é a equipe interna Syncro.

    Não é um AbstractUser porque AUTH_USER_MODEL já está fixado em
    accounts.SupportUser (Django só permite um). A autenticação usa uma
    JWTAuthentication dedicada (accounts.authentication.ClinicJWTAuthentication)
    que resolve o token para este model em vez de get_user_model().

    Sempre escopado a uma única clínica — nunca deve ser possível um
    ClinicUser acessar dado de outra clínica através de nenhuma rota.
    """

    class AuthProvider(models.TextChoices):
        LOCAL = 'local', 'Local (email/senha)'
        # LDAP é um add-on pago, vendido sob demanda — o campo já existe para não
        # exigir migração de schema quando for vendido, mas o fluxo de login LDAP
        # (bind contra o diretório do cliente) ainda não está implementado.
        LDAP = 'ldap', 'LDAP (add-on pago, não implementado)'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(
        'clinics.Clinic',
        on_delete=models.CASCADE,
        related_name='clinic_users',
        help_text='Clínica à qual este usuário pertence — nunca enxerga outra.',
    )
    email = models.EmailField()
    name = models.CharField(max_length=255)
    password = models.CharField(max_length=255, blank=True, help_text='Hash da senha (auth_provider=local)')
    auth_provider = models.CharField(max_length=10, choices=AuthProvider, default=AuthProvider.LOCAL)
    is_active = models.BooleanField(default=True)
    last_login = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('clinic', 'email')
        verbose_name = 'Usuário da Clínica'
        verbose_name_plural = 'Usuários da Clínica'
        indexes = [
            models.Index(fields=['clinic', 'email']),
        ]

    def __str__(self):
        return f'{self.email} @ {self.clinic.name}'

    def save(self, *args, **kwargs):
        # Normaliza para minúsculas antes de persistir: o login busca por
        # email__iexact, mas o unique_together (clinic, email) é case-sensitive
        # no banco — sem isso, 'a@x.com' e 'A@x.com' poderiam coexistir na
        # mesma clínica e o .get(email__iexact=...) do login levantaria
        # MultipleObjectsReturned (500) em vez de autenticar corretamente.
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        if self.auth_provider != self.AuthProvider.LOCAL:
            # LDAP não implementado ainda — nunca cai em fallback silencioso para local.
            return False
        return check_password(raw_password, self.password)

    def has_usable_password(self):
        """
        Espelha AbstractBaseUser.has_usable_password() — exigido pelo fluxo de
        reset de senha (TASK-BO-12, accounts/password_reset_clinic.py) pra não
        oferecer reset a ClinicUser sem senha local definida ainda (provisionado
        mas nunca ativado) nem a contas LDAP (senha não gerenciada aqui).
        """
        if self.auth_provider != self.AuthProvider.LOCAL:
            return False
        return bool(self.password) and is_password_usable(self.password)

    def get_email_field_name(self):
        """
        Duck-typing exigido por django.contrib.auth.tokens.PasswordResetTokenGenerator
        (Django 6 passou a incluir o e-mail no hash do token, pra invalidar
        links de reset pendentes se o e-mail mudar antes do usuário clicar —
        ver accounts/password_reset_clinic.py). AbstractBaseUser expõe isso
        como classmethod; aqui como instance method normal, tanto faz pro
        token generator (só chama user.get_email_field_name()).
        """
        return 'email'

    # Duck-typing exigido por DRF (throttling, permissions) — ClinicUser não é
    # AbstractBaseUser, mas request.user precisa expor esses dois atributos.
    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False
