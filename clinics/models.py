import uuid
from django.db import models


class Plan(models.TextChoices):
    STARTER = 'starter', 'Starter'
    PROFESSIONAL = 'professional', 'Professional'
    ENTERPRISE = 'enterprise', 'Enterprise'


class ClinicStatus(models.TextChoices):
    ACTIVE = 'active', 'Ativa'
    SUSPENDED = 'suspended', 'Suspensa'
    CANCELLED = 'cancelled', 'Cancelada'


class ProvisioningStatus(models.TextChoices):
    PENDING = 'pending', 'Pendente'
    WAITING_KEY = 'waiting_key', 'Aguardando chave pública'
    PROVISIONED = 'provisioned', 'Provisionado'
    FAILED = 'failed', 'Falhou'


class Clinic(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    license_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    plan = models.CharField(max_length=20, choices=Plan, default=Plan.STARTER)
    status = models.CharField(max_length=20, choices=ClinicStatus, default=ClinicStatus.ACTIVE)
    cnpj = models.CharField(max_length=18, unique=True, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    active_modules = models.JSONField(default=list, blank=True)
    gateway_url = models.URLField(max_length=255, blank=True, null=True)
    asaas_customer_id = models.CharField(max_length=50, blank=True, null=True, unique=True)
    asaas_subscription_id = models.CharField(max_length=50, blank=True, null=True, unique=True)

    # Chave pública RSA enviada pelo app desktop no registro
    # Usada para criptografar credenciais do banco — backoffice nunca vê a senha em claro
    public_key_pem = models.TextField(blank=True)

    # Provisionamento do banco
    db_name = models.CharField(max_length=63, unique=True, blank=True)
    db_user = models.CharField(max_length=63, unique=True, blank=True)
    # Senha criptografada com public_key_pem — ilegível sem a chave privada do app
    db_password_encrypted = models.TextField(blank=True)
    provisioning_status = models.CharField(
        max_length=20, choices=ProvisioningStatus, default=ProvisioningStatus.WAITING_KEY
    )
    provisioning_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    license_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Data de expiração da licença'
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Clínica'
        verbose_name_plural = 'Clínicas'

    def __str__(self):
        return f'{self.name} ({self.plan})'
