"""
LEGADO (BACFF-AVULSA-07) — NotionService não é mais chamado no fluxo ativo
de sincronização de tickets. Substituído por support.zoho_service.ZohoDeskService
porque o Notion vai passar a cobrar (limite de 1000 blocos no plano gratuito).

Mantido aqui, intacto e comentado apenas na chamada real (import ainda
funciona), como referência histórica — não apagar sem necessidade; não é
mais exercitado pelos testes de sincronização (ver support/tests_services.py,
que agora testa ZohoDeskService). Tickets antigos que só têm `notion_page_id`
continuam legíveis normalmente (campo preservado no model), só não são mais
escritos nem re-sincronizados.
"""
from notion_client import Client
from django.conf import settings
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)

class NotionService:
    """Service para integração com Notion (LEGADO — ver docstring do módulo)"""

    def __init__(self):
        self.client = Client(auth=settings.NOTION_API_KEY)
        self.database_id = settings.NOTION_DATABASE_ID

    def create_ticket_page(self, ticket):
        """Cria página no Notion quando ticket é criado"""
        try:
            page = self.client.pages.create(
                parent={"database_id": self.database_id},
                properties={
                    "Title": {
                        "title": [{"text": {"content": ticket.title}}]
                    },
                    "Clinic": {
                        "rich_text": [{"text": {"content": ticket.clinic.name}}]
                    },
                    "Status": {
                        "select": {"name": ticket.get_status_display()}
                    },
                    "Priority": {
                        "select": {"name": ticket.get_priority_display()}
                    },
                    "Description": {
                        "rich_text": [{"text": {"content": ticket.description}}]
                    },
                    "Assigned To": {
                        "rich_text": [
                            {"text": {"content": ticket.assigned_to.username}}
                            if ticket.assigned_to
                            else {"text": {"content": "Não atribuído"}}
                        ]
                    },
                    "Created At": {
                        "date": {"start": ticket.created_at.isoformat()}
                    },
                },
            )

            # Salva notion_page_id
            ticket.notion_page_id = page['id']
            ticket.save(update_fields=['notion_page_id'])

            logger.info(f"Ticket {ticket.id} criado no Notion: {page['id']}")
            return page['id']

        except Exception as e:
            logger.error(f"Erro ao criar página Notion: {str(e)}")
            return None
    
    def update_ticket_page(self, ticket):
        """Atualiza página no Notion quando ticket é modificado"""
        if not ticket.notion_page_id:
            return self.create_ticket_page(ticket)

        try:
            self.client.pages.update(
                page_id=ticket.notion_page_id,
                properties={
                    "Status": {
                        "select": {"name": ticket.get_status_display()}
                    },
                    "Priority": {
                        "select": {"name": ticket.get_priority_display()}
                    },
                    "Assigned To": {
                        "rich_text": [
                            {"text": {"content": ticket.assigned_to.username}}
                            if ticket.assigned_to
                            else {"text": {"content": "Não atribuído"}}
                        ]
                    },
                },
            )
            logger.info(f"Ticket {ticket.id} atualizado no Notion")
            return True

        except Exception as e:
            logger.error(f"Erro ao atualizar Notion: {str(e)}")
            return False
        
    def add_message_to_notion(self, ticket, message):
        """Adiciona comentário no Notion"""
        if not ticket.notion_page_id:
            return False

        try:
            # Notion API não tem "comments" nativo, então adiciona no description
            self.client.blocks.children.append(
                block_id=ticket.notion_page_id,
                children=[
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {
                                    "type": "text",
                                    "text": {
                                        "content": f"💬 {message.author.username}: {message.message}"
                                    },
                                }
                            ],
                        },
                    }
                ],
            )
            logger.info(f"Mensagem adicionada ao Notion para ticket {ticket.id}")
            return True

        except Exception as e:
            logger.error(f"Erro ao adicionar mensagem Notion: {str(e)}")
            return False