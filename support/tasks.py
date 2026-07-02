import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sync_ticket_to_notion(self, ticket_id):
    """
    Sincroniza um ticket com o Notion de forma assíncrona.
    Cria a página se ainda não existir; atualiza se já houver notion_page_id.
    Retry automático em caso de falha de rede (até 3 vezes, 60 s de intervalo).
    """
    from .models import Ticket
    from .services import NotionService

    try:
        ticket = Ticket.objects.select_related('clinic', 'assigned_to').get(pk=ticket_id)
    except Ticket.DoesNotExist:
        logger.warning("sync_ticket_to_notion: ticket %s não encontrado; abortando.", ticket_id)
        return

    service = NotionService()
    try:
        if not ticket.notion_page_id:
            service.create_ticket_page(ticket)
        else:
            service.update_ticket_page(ticket)
    except Exception as exc:
        logger.error(
            "sync_ticket_to_notion: erro ao sincronizar ticket %s com Notion. Erro: %s",
            ticket_id,
            str(exc),
        )
        raise self.retry(exc=exc)
