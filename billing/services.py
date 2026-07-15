"""
TASK-BO-11: cobrança recorrente Asaas — plano único R$199,90/mês com dois
descontos possíveis "por cima" (lançamento e fidelidade), ambos decididos
manualmente por quem faz onboarding/CS — nunca self-service.

LGPD: nenhuma função aqui envia dado de paciente ao Asaas ou para log.
`description` é sempre texto genérico de plano; `externalReference` é o
UUID da Clinic (não nome em texto livre).
"""
import logging
from decimal import Decimal

from django.utils import timezone

from clinics.models import Clinic
from .asaas import AsaasClient

logger = logging.getLogger(__name__)

PRICE_STANDARD = Decimal('199.90')
PRICE_PROMOTIONAL = Decimal('99.90')
SUBSCRIPTION_ANNIVERSARY_DAYS = 365
SUBSCRIPTION_DESCRIPTION = 'Plano Syncro Health — mensalidade'


def create_clinic_subscription(clinic: Clinic, billing_type: str = 'PIX') -> Clinic:
    """
    Cria (ou recria) a assinatura recorrente no Asaas para uma clínica.

    O valor cobrado é decidido pelo estado atual de `clinic.preco_promocional`
    (marcado manualmente no Django Admin antes de chamar esta função):
    - True  -> R$99,90 (desconto de lançamento, exige vaga disponível)
    - False -> R$199,90 (padrão)

    Não recebe nenhum dado de paciente. `description` é genérica por plano;
    `externalReference` é o UUID da clínica.
    """
    if clinic.preco_promocional and not Clinic.promotional_slots_available(exclude_pk=clinic.pk):
        # Checagem de segurança redundante ao clean() do model — nunca cria
        # assinatura promocional além das 30 vagas, mesmo que o flag tenha
        # sido setado via fixture/migração/import em massa.
        raise ValueError(
            'Limite de vagas do desconto de lançamento atingido; '
            'não é possível criar assinatura promocional para esta clínica.'
        )

    value = PRICE_PROMOTIONAL if clinic.preco_promocional else PRICE_STANDARD

    client = AsaasClient()

    if not clinic.asaas_customer_id:
        clinic.asaas_customer_id = client.create_customer(
            name=clinic.name,
            cnpj_cpf=clinic.cnpj,
            email=clinic.contact_email,
            phone=clinic.contact_phone,
        )

    subscription_id = client.create_subscription(
        customer_id=clinic.asaas_customer_id,
        value=float(value),
        billing_type=billing_type,
        description=SUBSCRIPTION_DESCRIPTION,
        external_reference=str(clinic.id),
    )

    clinic.asaas_subscription_id = subscription_id
    clinic.subscription_started_at = timezone.now()
    clinic.price_adjusted_at = None
    clinic.save(update_fields=[
        'asaas_customer_id',
        'asaas_subscription_id',
        'subscription_started_at',
        'price_adjusted_at',
        'updated_at',
    ])

    logger.info(
        "create_clinic_subscription: assinatura criada para clinic_id=%s (valor=%s, promocional=%s)",
        clinic.id, value, clinic.preco_promocional,
    )
    return clinic


def adjust_promotional_pricing() -> dict:
    """
    Job diário (idempotente): reajusta pra R$199,90 as clínicas promocionais
    que completaram 12 meses de assinatura e não têm desconto de fidelidade
    marcado manualmente.

    Critério de elegibilidade:
    - subscription_started_at há >= 365 dias
    - preco_promocional=True
    - price_adjusted_at is NULL (ainda não processada — garante idempotência)

    Se desconto_fidelidade_ano2=True: NÃO sobe o valor no Asaas, só marca
    price_adjusted_at (decisão manual de CS já tomada, mantém o preço).
    Caso contrário: chama update_subscription_value pra R$199,90 e marca
    price_adjusted_at.

    Retorna um resumo de contagens (sem nenhum dado de paciente/PHI).
    """
    cutoff = timezone.now() - timezone.timedelta(days=SUBSCRIPTION_ANNIVERSARY_DAYS)

    eligible = Clinic.objects.filter(
        preco_promocional=True,
        price_adjusted_at__isnull=True,
        subscription_started_at__isnull=False,
        subscription_started_at__lte=cutoff,
    )

    adjusted = 0
    kept_loyalty = 0
    errors = 0
    client = AsaasClient()

    for clinic in eligible:
        try:
            if clinic.desconto_fidelidade_ano2:
                clinic.price_adjusted_at = timezone.now()
                clinic.save(update_fields=['price_adjusted_at', 'updated_at'])
                kept_loyalty += 1
                logger.info(
                    "adjust_promotional_pricing: clinic_id=%s mantém desconto (fidelidade).",
                    clinic.id,
                )
            else:
                if clinic.asaas_subscription_id:
                    client.update_subscription_value(clinic.asaas_subscription_id, float(PRICE_STANDARD))
                clinic.price_adjusted_at = timezone.now()
                clinic.save(update_fields=['price_adjusted_at', 'updated_at'])
                adjusted += 1
                logger.info(
                    "adjust_promotional_pricing: clinic_id=%s reajustada para R$%s.",
                    clinic.id, PRICE_STANDARD,
                )
        except Exception as exc:
            errors += 1
            logger.error(
                "adjust_promotional_pricing: erro ao processar clinic_id=%s. Erro: %s",
                clinic.id, str(exc),
            )

    return {'adjusted': adjusted, 'kept_loyalty': kept_loyalty, 'errors': errors}
