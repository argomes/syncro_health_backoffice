import json
from unittest.mock import Mock, patch, MagicMock
from django.test import TestCase
from django.utils import timezone
from django.db.models.signals import post_save
from decimal import Decimal

from accounts.models import SupportUser
from clinics.models import Clinic, ClinicStatus, Plan as ClinicsEnum
from support.models import Ticket, TicketMessage, sync_ticket_to_notion
from support.services import NotionService


def make_clinic(name='Clínica Notion'):
    """Helper para criar clínica"""
    return Clinic.objects.create(
        name=name,
        slug=f'clinica-notion-{timezone.now().timestamp()}',
        plan=ClinicsEnum.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj='12.345.678/0001-99',
        db_name='clinic_notion',
        db_user='u_notion',
    )


class NotionServiceTest(TestCase):
    """Testes do NotionService (Notion integration)"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        post_save.disconnect(sync_ticket_to_notion, sender=Ticket)

    def setUp(self):
        self.clinic = make_clinic()
        self.user = SupportUser.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass'
        )
        self.ticket = Ticket.objects.create(
            clinic=self.clinic,
            title='Teste Notion',
            description='Testando integração',
            status='open',
            priority='high',
            notion_page_id='',
        )

    @patch('support.services.Client')
    def test_create_ticket_page_success(self, mock_client_class):
        """Criar página no Notion com sucesso"""
        # Mock do Notion
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.pages.create.return_value = {
            'id': 'page-123-abc'
        }

        # Chamar service
        service = NotionService()
        result = service.create_ticket_page(self.ticket)

        # Validar
        self.assertEqual(result, 'page-123-abc')
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.notion_page_id, 'page-123-abc')
        mock_client.pages.create.assert_called_once()

    @patch('support.services.Client')
    def test_create_ticket_page_error_handling(self, mock_client_class):
        """Erro ao criar página no Notion"""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.pages.create.side_effect = Exception('Notion API error')

        service = NotionService()
        result = service.create_ticket_page(self.ticket)

        # Deve retornar None em erro
        self.assertIsNone(result)
        self.ticket.refresh_from_db()
        # notion_page_id permanece vazio após erro
        self.assertEqual(self.ticket.notion_page_id, '')

    @patch('support.services.Client')
    def test_update_ticket_page_success(self, mock_client_class):
        """Atualizar página no Notion"""
        # Ticket já com notion_page_id
        self.ticket.notion_page_id = 'page-existing'
        self.ticket.save()

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.pages.update.return_value = {'id': 'page-existing'}

        service = NotionService()
        result = service.update_ticket_page(self.ticket)

        self.assertTrue(result)
        mock_client.pages.update.assert_called_once()

    @patch('support.services.Client')
    def test_update_ticket_page_creates_if_missing(self, mock_client_class):
        """Se notion_page_id não existe, cria novo"""
        # Ticket SEM notion_page_id (string vazia)
        self.ticket.notion_page_id = ''
        self.ticket.save()

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.pages.create.return_value = {'id': 'page-new'}

        service = NotionService()
        result = service.update_ticket_page(self.ticket)

        # Deve chamar create, não update
        mock_client.pages.create.assert_called_once()
        mock_client.pages.update.assert_not_called()

    @patch('support.services.Client')
    def test_add_message_to_notion_success(self, mock_client_class):
        """Adicionar mensagem no Notion"""
        self.ticket.notion_page_id = 'page-123'
        self.ticket.save()

        message = TicketMessage.objects.create(
            ticket=self.ticket,
            author=self.user,
            message='Teste mensagem',
        )

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.blocks.children.append.return_value = {'id': 'block-123'}

        service = NotionService()
        result = service.add_message_to_notion(self.ticket, message)

        self.assertTrue(result)
        mock_client.blocks.children.append.assert_called_once()

    @patch('support.services.Client')
    def test_add_message_to_notion_no_page_id(self, mock_client_class):
        """Não adicionar mensagem se não há notion_page_id"""
        message = TicketMessage.objects.create(
            ticket=self.ticket,
            author=self.user,
            message='Teste',
        )

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        service = NotionService()
        result = service.add_message_to_notion(self.ticket, message)

        self.assertFalse(result)
        mock_client.blocks.children.append.assert_not_called()

    @patch('support.services.Client')
    def test_add_message_to_notion_error_handling(self, mock_client_class):
        """Erro ao adicionar mensagem"""
        self.ticket.notion_page_id = 'page-123'
        self.ticket.save()

        message = TicketMessage.objects.create(
            ticket=self.ticket,
            author=self.user,
            message='Teste',
        )

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.blocks.children.append.side_effect = Exception('API error')

        service = NotionService()
        result = service.add_message_to_notion(self.ticket, message)

        self.assertFalse(result)