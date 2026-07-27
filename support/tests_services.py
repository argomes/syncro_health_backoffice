from unittest.mock import MagicMock, patch
from django.test import TestCase, override_settings
from django.utils import timezone
from django.db.models.signals import post_save

from accounts.models import SupportUser
from clinics.models import Clinic, ClinicStatus, Plan as ClinicsEnum
from support.models import Ticket, TicketMessage, sync_ticket_to_zoho
from support.zoho_service import ZohoDeskService


def make_clinic(name='Clínica Zoho'):
    """Helper para criar clínica"""
    return Clinic.objects.create(
        name=name,
        slug=f'clinica-zoho-{timezone.now().timestamp()}',
        plan=ClinicsEnum.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj='12.345.678/0001-99',
        db_name='clinic_zoho',
        db_user='u_zoho',
    )


ZOHO_SETTINGS = dict(
    ZOHO_DESK_CLIENT_ID='client-id',
    ZOHO_DESK_CLIENT_SECRET='client-secret',
    ZOHO_DESK_REFRESH_TOKEN='refresh-token',
    ZOHO_DESK_ORG_ID='org-id',
    ZOHO_DESK_DEPARTMENT_ID='dept-id',
    ZOHO_DESK_ACCOUNTS_URL='https://accounts.zoho.com',
    ZOHO_DESK_API_BASE_URL='https://desk.zoho.com/api/v1',
)


@override_settings(**ZOHO_SETTINGS)
class ZohoDeskServiceTest(TestCase):
    """
    Testes do ZohoDeskService (substitui NotionServiceTest — BACFF-AVULSA-07).

    Nunca chama a API real: `requests.post`/`requests.patch` são sempre
    mockados, inclusive a troca de refresh_token por access_token.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Mesmo padrão de tests.py/tests_admin.py: sem desconectar o signal,
        # cada Ticket.objects.create() dispara sync_ticket_to_zoho e, com
        # CELERY_TASK_ALWAYS_EAGER=True, chamaria a API real do Zoho Desk.
        post_save.disconnect(sync_ticket_to_zoho, sender=Ticket)

    @classmethod
    def tearDownClass(cls):
        post_save.connect(sync_ticket_to_zoho, sender=Ticket)
        super().tearDownClass()

    def setUp(self):
        self.clinic = make_clinic()
        self.user = SupportUser.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass'
        )
        self.ticket = Ticket.objects.create(
            clinic=self.clinic,
            title='Teste Zoho',
            description='Testando integração',
            status='open',
            priority='high',
            zoho_ticket_id='',
        )

    def _mock_token_response(self, mock_post):
        token_response = MagicMock()
        token_response.json.return_value = {'access_token': 'fake-access-token'}
        token_response.raise_for_status.return_value = None
        return token_response

    @patch('support.zoho_service.requests.post')
    def test_create_ticket_success(self, mock_post):
        """Criar ticket no Zoho Desk com sucesso"""
        token_response = self._mock_token_response(mock_post)
        ticket_response = MagicMock()
        ticket_response.json.return_value = {'id': 'zoho-ticket-123'}
        ticket_response.raise_for_status.return_value = None

        # 1ª chamada = token, 2ª chamada = criação do ticket
        mock_post.side_effect = [token_response, ticket_response]

        service = ZohoDeskService()
        result = service.create_ticket(self.ticket)

        self.assertEqual(result, 'zoho-ticket-123')
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.zoho_ticket_id, 'zoho-ticket-123')
        self.assertEqual(mock_post.call_count, 2)

    @patch('support.zoho_service.requests.post')
    def test_create_ticket_error_handling(self, mock_post):
        """Erro ao criar ticket no Zoho Desk não propaga exceção"""
        token_response = self._mock_token_response(mock_post)
        mock_post.side_effect = [token_response, Exception('Zoho API error')]

        service = ZohoDeskService()
        result = service.create_ticket(self.ticket)

        self.assertIsNone(result)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.zoho_ticket_id, '')

    @patch('support.zoho_service.requests.patch')
    @patch('support.zoho_service.requests.post')
    def test_update_ticket_success(self, mock_post, mock_patch):
        """Atualizar ticket já existente no Zoho Desk"""
        self.ticket.zoho_ticket_id = 'zoho-existing'
        self.ticket.save()

        mock_post.return_value = self._mock_token_response(mock_post)
        patch_response = MagicMock()
        patch_response.raise_for_status.return_value = None
        mock_patch.return_value = patch_response

        service = ZohoDeskService()
        result = service.update_ticket(self.ticket)

        self.assertTrue(result)
        mock_patch.assert_called_once()

    @patch('support.zoho_service.requests.post')
    def test_update_ticket_creates_if_missing(self, mock_post):
        """Se zoho_ticket_id não existe, cria novo ticket em vez de atualizar"""
        self.ticket.zoho_ticket_id = ''
        self.ticket.save()

        token_response = self._mock_token_response(mock_post)
        create_response = MagicMock()
        create_response.json.return_value = {'id': 'zoho-new'}
        create_response.raise_for_status.return_value = None
        mock_post.side_effect = [token_response, create_response]

        service = ZohoDeskService()
        result = service.update_ticket(self.ticket)

        self.assertEqual(result, 'zoho-new')
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.zoho_ticket_id, 'zoho-new')

    @patch('support.zoho_service.requests.post')
    def test_add_comment_success(self, mock_post):
        """Adicionar comentário no ticket do Zoho Desk"""
        self.ticket.zoho_ticket_id = 'zoho-123'
        self.ticket.save()

        message = TicketMessage.objects.create(
            ticket=self.ticket,
            author=self.user,
            message='Teste mensagem',
        )

        token_response = self._mock_token_response(mock_post)
        comment_response = MagicMock()
        comment_response.json.return_value = {'id': 'comment-123'}
        comment_response.raise_for_status.return_value = None
        mock_post.side_effect = [token_response, comment_response]

        service = ZohoDeskService()
        result = service.add_comment(self.ticket, message)

        self.assertTrue(result)
        self.assertEqual(mock_post.call_count, 2)

    @patch('support.zoho_service.requests.post')
    def test_add_comment_no_ticket_id(self, mock_post):
        """Não adiciona comentário se não há zoho_ticket_id"""
        message = TicketMessage.objects.create(
            ticket=self.ticket,
            author=self.user,
            message='Teste',
        )

        service = ZohoDeskService()
        result = service.add_comment(self.ticket, message)

        self.assertFalse(result)
        mock_post.assert_not_called()

    @patch('support.zoho_service.requests.post')
    def test_add_comment_error_handling(self, mock_post):
        """Erro ao adicionar comentário não propaga exceção"""
        self.ticket.zoho_ticket_id = 'zoho-123'
        self.ticket.save()

        message = TicketMessage.objects.create(
            ticket=self.ticket,
            author=self.user,
            message='Teste',
        )

        token_response = self._mock_token_response(mock_post)
        mock_post.side_effect = [token_response, Exception('API error')]

        service = ZohoDeskService()
        result = service.add_comment(self.ticket, message)

        self.assertFalse(result)
