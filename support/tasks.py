import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sync_ticket_to_zoho(self, ticket_id):
    """
    Sincroniza um ticket com o Zoho Desk de forma assíncrona.
    Cria o ticket se ainda não existir; atualiza se já houver zoho_ticket_id.
    Retry automático em caso de falha de rede (até 3 vezes, 60 s de intervalo).

    Substitui sync_ticket_to_notion (BACFF-AVULSA-07). Tickets antigos que só
    têm `notion_page_id` (criados antes da troca) não são migrados aqui —
    ganham um `zoho_ticket_id` novo na próxima vez que forem salvos, como
    qualquer ticket novo.
    """
    from .models import Ticket
    from .zoho_service import ZohoDeskService

    try:
        ticket = Ticket.objects.select_related('clinic', 'assigned_to').get(pk=ticket_id)
    except Ticket.DoesNotExist:
        logger.warning("sync_ticket_to_zoho: ticket %s não encontrado; abortando.", ticket_id)
        return

    service = ZohoDeskService()
    try:
        if not ticket.zoho_ticket_id:
            service.create_ticket(ticket)
        else:
            service.update_ticket(ticket)
    except Exception as exc:
        logger.error(
            "sync_ticket_to_zoho: erro ao sincronizar ticket %s com Zoho Desk. Erro: %s",
            ticket_id,
            str(exc),
        )
        raise self.retry(exc=exc)
