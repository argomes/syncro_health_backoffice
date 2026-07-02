import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def sync_modules_to_edge(self, clinic_id):
    """
    Sincroniza os módulos de uma clínica com o Gateway Edge de forma assíncrona.
    Retry automático em caso de falha de rede (até 3 vezes, 30 s de intervalo).
    """
    from .models import Clinic
    from .services import sync_clinic_modules

    try:
        clinic = Clinic.objects.get(pk=clinic_id)
    except Clinic.DoesNotExist:
        logger.warning("sync_modules_to_edge: clínica %s não encontrada; abortando.", clinic_id)
        return

    try:
        sync_clinic_modules(clinic)
    except Exception as exc:
        logger.error(
            "sync_modules_to_edge: erro ao sincronizar clínica %s. Erro: %s",
            clinic_id,
            str(exc),
        )
        raise self.retry(exc=exc)
