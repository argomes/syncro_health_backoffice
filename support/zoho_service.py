"""
Integração com Zoho Desk (substitui o NotionService — ver support/services.py
para o código antigo, mantido comentado/histórico).

Autenticação: OAuth2 "self client" server-to-server. Zoho Desk não oferece
API key simples para este caso — o modelo padrão deles é um refresh_token de
longa duração (gerado uma vez via Self Client no Zoho API Console) trocado
por um access_token de curta duração (~1h) a cada chamada. Não há fluxo de
login de usuário aqui: o refresh_token já concede o escopo necessário
(Desk.tickets.ALL, Desk.tasks.ALL etc.) e fica salvo em env var, igual ao
NOTION_API_KEY antes.

Endpoints usados (região configurável via ZOHO_DESK_ACCOUNTS_URL /
ZOHO_DESK_API_BASE_URL, pois contas .com/.eu/.in têm domínios diferentes):
  - POST {accounts_url}/oauth/v2/token           (refresh -> access_token)
  - POST {api_base_url}/tickets                  (criar ticket)
  - PATCH {api_base_url}/tickets/{id}             (atualizar ticket)
  - POST {api_base_url}/tickets/{id}/comments     (comentário — nativo,
    sem o workaround de "blocks" que o Notion exigia)
"""
from django.conf import settings
import logging
import requests

logger = logging.getLogger(__name__)


class ZohoDeskService:
    """Service para integração com Zoho Desk (mesma interface pública do antigo NotionService)"""

    TOKEN_URL_PATH = '/oauth/v2/token'

    def __init__(self):
        self.client_id = settings.ZOHO_DESK_CLIENT_ID
        self.client_secret = settings.ZOHO_DESK_CLIENT_SECRET
        self.refresh_token = settings.ZOHO_DESK_REFRESH_TOKEN
        self.org_id = settings.ZOHO_DESK_ORG_ID
        self.department_id = settings.ZOHO_DESK_DEPARTMENT_ID
        self.accounts_url = settings.ZOHO_DESK_ACCOUNTS_URL
        self.api_base_url = settings.ZOHO_DESK_API_BASE_URL

    def _get_access_token(self):
        """
        Troca o refresh_token de longa duração por um access_token efêmero.
        Server-to-server puro — sem redirect/login de usuário.
        """
        response = requests.post(
            f'{self.accounts_url}{self.TOKEN_URL_PATH}',
            params={
                'refresh_token': self.refresh_token,
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'grant_type': 'refresh_token',
            },
            timeout=10,
        )
        response.raise_for_status()
        return response.json()['access_token']

    def _headers(self):
        return {
            'Authorization': f'Zoho-oauthtoken {self._get_access_token()}',
            'orgId': self.org_id,
            'Content-Type': 'application/json',
        }

    def _contact_payload(self, ticket):
        """
        Monta o campo 'contact' exigido pelo Zoho Desk (contactId é
        obrigatório na criação de ticket — confirmado por teste real contra
        a API, INVALID_DATA/contactId missing). A API casa/cria o contato
        automaticamente a partir de {lastName, email}, sem precisar de um
        contactId pré-existente.

        Fallback em 3 níveis, pois created_by é opcional (SET_NULL) e
        clinic.contact_email pode estar em branco: quem criou o ticket ->
        e-mail de contato da clínica -> e-mail padrão do sistema (nunca
        deixa a criação falhar por falta de e-mail).
        """
        if ticket.created_by and ticket.created_by.email:
            name = ticket.created_by.get_full_name() or ticket.created_by.username
            email = ticket.created_by.email
        elif ticket.clinic.contact_email:
            name = ticket.clinic.name
            email = ticket.clinic.contact_email
        else:
            name = ticket.clinic.name
            email = settings.DEFAULT_FROM_EMAIL

        return {'lastName': name, 'email': email}

    def create_ticket(self, ticket):
        """Cria ticket no Zoho Desk quando o Ticket local é criado"""
        try:
            payload = {
                'subject': ticket.title,
                'description': ticket.description,
                'departmentId': self.department_id,
                'status': ticket.get_status_display(),
                'priority': ticket.get_priority_display(),
                'contact': self._contact_payload(ticket),
                'cf': {
                    'cf_clinica': ticket.clinic.name,
                },
            }

            response = requests.post(
                f'{self.api_base_url}/tickets',
                headers=self._headers(),
                json=payload,
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            ticket.zoho_ticket_id = data['id']
            ticket.save(update_fields=['zoho_ticket_id'])

            logger.info(f"Ticket {ticket.id} criado no Zoho Desk: {data['id']}")
            return data['id']

        except Exception as e:
            logger.error(f"Erro ao criar ticket no Zoho Desk: {str(e)}")
            return None

    def update_ticket(self, ticket):
        """Atualiza ticket no Zoho Desk quando o Ticket local é modificado"""
        if not ticket.zoho_ticket_id:
            return self.create_ticket(ticket)

        try:
            payload = {
                'status': ticket.get_status_display(),
                'priority': ticket.get_priority_display(),
            }

            response = requests.patch(
                f'{self.api_base_url}/tickets/{ticket.zoho_ticket_id}',
                headers=self._headers(),
                json=payload,
                timeout=10,
            )
            response.raise_for_status()
            logger.info(f"Ticket {ticket.id} atualizado no Zoho Desk")
            return True

        except Exception as e:
            logger.error(f"Erro ao atualizar ticket no Zoho Desk: {str(e)}")
            return False

    def add_comment(self, ticket, message):
        """
        Adiciona comentário no ticket do Zoho Desk. Zoho Desk tem comentários
        nativos, então aqui não há o workaround de "blocks" que o Notion
        exigia (era preciso anexar parágrafos na própria página).
        """
        if not ticket.zoho_ticket_id:
            return False

        try:
            payload = {
                'content': f'{message.author.username}: {message.message}' if message.author else message.message,
                'isPublic': False,
            }

            response = requests.post(
                f'{self.api_base_url}/tickets/{ticket.zoho_ticket_id}/comments',
                headers=self._headers(),
                json=payload,
                timeout=10,
            )
            response.raise_for_status()
            logger.info(f"Comentário adicionado ao Zoho Desk para ticket {ticket.id}")
            return True

        except Exception as e:
            logger.error(f"Erro ao adicionar comentário no Zoho Desk: {str(e)}")
            return False
