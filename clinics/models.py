import uuid
from django.core.exceptions import ValidationError
from django.db import models

PROMOTIONAL_SLOTS = 30


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
    # CNES (Cadastro Nacional de Estabelecimentos de Saúde) — código numérico
    # nacional (padrão 7 dígitos) exigido junto com o CNPJ em toda guia TISS
    # (ver `cnpj_and_cnes_required_for_tiss` no gateway). Não é unique: uma
    # mesma clínica pode ter múltiplas unidades cadastradas com o mesmo CNES
    # não é a regra, mas nada nas normas TISS garante unicidade global aqui
    # como garante para CNPJ — por isso não replicamos a constraint UNIQUE.
    cnes = models.CharField(
        max_length=7,
        blank=True,
        default='',
        # db_default (não só default do Python) grava o DEFAULT '' no
        # próprio schema — sem isso, o Django SQLite backend só usa o
        # default no backfill desta migration, e qualquer INSERT que não
        # liste explicitamente a coluna `cnes` (ex.: código rodando contra
        # um model state anterior a esta migration, como em testes de
        # migração) quebra com NOT NULL constraint.
        db_default='',
        help_text='Código CNES (Cadastro Nacional de Estabelecimentos de Saúde), 7 dígitos numéricos — obrigatório para geração de guias TISS.',
    )
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    active_modules = models.JSONField(default=list, blank=True)
    gateway_url = models.URLField(max_length=255, blank=True, null=True)
    asaas_customer_id = models.CharField(max_length=50, blank=True, null=True, unique=True)
    asaas_subscription_id = models.CharField(max_length=50, blank=True, null=True, unique=True)

    # TASK-BO-11: cobrança recorrente — plano único R$199,90, com dois
    # descontos possíveis "por cima" (nunca self-service, sempre decisão
    # manual de quem faz onboarding/CS no Django Admin).
    subscription_started_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Data de início da assinatura recorrente no Asaas (base para o aniversário de 12 meses).'
    )
    preco_promocional = models.BooleanField(
        default=False,
        help_text='Desconto de lançamento (R$99,90/mês no 1º ano) — limitado às 30 primeiras clínicas.'
    )
    desconto_fidelidade_ano2 = models.BooleanField(
        default=False,
        help_text=(
            'Decisão manual de CS: mantém o desconto no 2º ano em diante. '
            'Não é calculado automaticamente — julgamento qualitativo sobre '
            'engajamento/feedback da clínica durante o 1º ano.'
        )
    )
    price_adjusted_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Marca quando o job de reajuste de 12 meses já processou esta clínica (evita reajuste duplicado).'
    )

    # EDGW-052: pré-requisito de BACFF-AVULSA-09 (tela de configuração ainda
    # não implementada) — quando True, só admin da clínica pode ver/criar
    # tickets de suporte (hoje qualquer role no desktop pode reportar erro
    # via POST /api/support/error-reports/). Persistido e exposto em
    # get_license_info (clinics/views.py) — mesmo canal já usado por
    # `active_modules` para chegar ao gateway — mas a checagem de RBAC em si
    # é escopo de outra task.
    support_ticket_restricted_to_admin = models.BooleanField(
        default=False,
        help_text='Restringe criação/visualização de tickets de suporte ao admin da clínica (BACFF-AVULSA-09).'
    )

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

    @classmethod
    def promotional_slots_available(cls, exclude_pk=None) -> bool:
        """
        True se ainda há vaga de desconto de lançamento (30 primeiras
        clínicas). Verificado no cadastro manual antes de marcar
        `preco_promocional=True` numa clínica.

        `exclude_pk` exclui a própria clínica da contagem — necessário
        para clínicas que já são promocionais e estão sendo reprocessadas
        (ex: criação de assinatura para uma das 30 já marcadas).
        """
        qs = cls.objects.filter(preco_promocional=True)
        if exclude_pk is not None:
            qs = qs.exclude(pk=exclude_pk)
        return qs.count() < PROMOTIONAL_SLOTS

    def clean(self):
        super().clean()
        if self.preco_promocional:
            if not Clinic.promotional_slots_available(exclude_pk=self.pk):
                raise ValidationError({
                    'preco_promocional': (
                        f'Limite de {PROMOTIONAL_SLOTS} vagas do desconto de lançamento já '
                        'foi atingido. Não é possível marcar mais clínicas como promocionais.'
                    )
                })
