import hmac
import json
import logging
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from clinics.models import Clinic, ClinicStatus

logger = logging.getLogger(__name__)


def _check_webhook_rate_limit(ip: str, limit: int = 120, window: int = 60) -> bool:
    """Returns True if request is within limit, False if rate limited."""
    key = f'webhook_rl:{ip}'
    count = cache.get(key, 0)
    if count >= limit:
        return False
    cache.set(key, count + 1, timeout=window)
    return True


@csrf_exempt
def asaas_webhook(request):
    """
    POST /api/v1/billing/webhook/asaas/
    Webhook público para receber notificações do ASAAS.
    Valida o header asaas-access-token antes de processar qualquer evento.
    Processa confirmações de pagamento (ativação) e atrasos (bloqueio/suspensão).
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', ''))
    ip = ip.split(',')[0].strip()
    if not _check_webhook_rate_limit(ip):
        logger.warning("asaas_webhook: rate limit atingido. IP=%s", ip)
        return HttpResponse(status=429)

    # Validação de assinatura: timing-safe compare contra ASAAS_WEBHOOK_TOKEN
    expected_token = getattr(settings, 'ASAAS_WEBHOOK_TOKEN', '')
    received_token = request.headers.get('asaas-access-token', '')
    if not expected_token or not hmac.compare_digest(
        expected_token.encode('utf-8'),
        received_token.encode('utf-8'),
    ):
        logger.warning(
            "asaas_webhook: token inválido ou ausente. IP=%s",
            request.META.get('REMOTE_ADDR'),
        )
        return HttpResponse(status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    event = data.get('event')
    payment = data.get('payment') or {}
    subscription_id = payment.get('subscription')
    customer_id = payment.get('customer')

    logger.info("Asaas Webhook: evento %s recebido para sub=%s, customer=%s", event, subscription_id, customer_id)

    # Localiza a clínica associada
    clinic = None
    if subscription_id:
        clinic = Clinic.objects.filter(asaas_subscription_id=subscription_id).first()
    if not clinic and customer_id:
        clinic = Clinic.objects.filter(asaas_customer_id=customer_id).first()

    if not clinic:
        logger.warning("Asaas Webhook: clínica não encontrada para sub=%s ou customer=%s", subscription_id, customer_id)
        return JsonResponse({'status': 'ignored_clinic_not_found'}, status=200)

    # Processamento dos eventos principais
    if event in ['PAYMENT_RECEIVED', 'PAYMENT_CONFIRMED']:
        logger.info("Asaas Webhook: pagamento confirmado. Ativando clínica %s", clinic.name)
        if clinic.status != ClinicStatus.ACTIVE:
            clinic.status = ClinicStatus.ACTIVE
            clinic.save(update_fields=['status'])

    elif event in ['PAYMENT_OVERDUE', 'PAYMENT_DELETED']:
        logger.warning("Asaas Webhook: pagamento em atraso/deletado. Suspendendo clínica %s", clinic.name)
        if clinic.status != ClinicStatus.SUSPENDED:
            clinic.status = ClinicStatus.SUSPENDED
            clinic.save(update_fields=['status'])

    return JsonResponse({'ok': True}, status=200)
