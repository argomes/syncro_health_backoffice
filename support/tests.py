import uuid
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from clinics.models import Clinic, ClinicStatus, Plan, ProvisioningStatus
from accounts.models import SupportUser, ClinicAccess
from .models import Ticket, TicketMessage


def make_clinic(name='Clínica Teste'):
    """Helper para criar clínica com valores únicos"""
    return Clinic.objects.create(
        name=name,
        slug=f'clinica-{uuid.uuid4().hex[:8]}',
        plan=Plan.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
        db_name=f'clinic_{uuid.uuid4().hex[:8]}',  # ✅ Único!
        db_user=f'u_{uuid.uuid4().hex[:8]}',       # ✅ Único!
        provisioning_status=ProvisioningStatus.PROVISIONED,
    )
    
    
class TicketAPITest(TestCase):  # ✅ TestCase, não APITestCase
    def setUp(self):
        self.client = APIClient()  # ✅ APIClient simples
        self.clinic = make_clinic()
        self.support_user = SupportUser.objects.create_user(
            username='support',
            email='support@test.com',
            password='pass123',
            role='support'
        )

    def _create_ticket(self, **kwargs):
        """Helper para criar ticket"""
        defaults = {
            'clinic': self.clinic,
            'created_by': self.support_user,
            'title': 'Problema no app',
            'description': 'Não consigo fazer login',
            'status': 'open',
            'priority': 'medium',
        }
        defaults.update(kwargs)
        return Ticket.objects.create(**defaults)

    def test_create_ticket_via_api(self):
        """POST /api/support/tickets/ — criar ticket"""
        # ✅ Sem force_authenticate, usar JWT token real ou license_key
        response = self.client.post(
            '/api/support/tickets/',
            {
                'clinic': str(self.clinic.id),
                'title': 'Bug crítico', 
                'description': 'App crasheia ao abrir',
                'priority': 'critical',
            },
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),  # ✅ Auth por clinic
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Ticket.objects.filter(clinic=self.clinic).count(), 1)

    def test_list_tickets(self):
        """GET /api/support/tickets/ — listar tickets"""
        self._create_ticket()
        self._create_ticket(priority='high')

        response = self.client.get(
            '/api/support/tickets/',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data['results']), 2)

    def test_resolve_ticket(self):
        """POST /api/support/tickets/:id/resolve/ — resolver ticket"""
        ticket = self._create_ticket()

        response = self.client.post(
            f'/api/support/tickets/{ticket.id}/resolve/',
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'resolved')
        self.assertIsNotNone(ticket.resolved_at)

    def test_invalid_license_key_returns_4xx(self):
        """Sem license key válida = 401/403"""
        response = self.client.post(
            '/api/support/tickets/',
            {'title': 'Test'},
            format='json',
            HTTP_X_LICENSE_KEY=str(uuid.uuid4()),  # ✅ UUID válido mas não registrado
        )
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_missing_license_key_returns_4xx(self):
        """Sem header de license key = 401/403"""
        response = self.client.post(
            '/api/support/tickets/',
            {'title': 'Test'},
            format='json',
            # ❌ Sem HTTP_X_LICENSE_KEY
        )
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_create_ticket_mass_assignment_denied(self):
        """
        BO-SEC-009: support user com ClinicAccess só na Clínica A não pode
        criar ticket em nome da Clínica B via payload (`clinic` gravável no
        TicketCreateSerializer + perform_create sem checagem = mass assignment).
        """
        clinic_b = make_clinic(name='Clínica B Mass Assignment')
        ClinicAccess.objects.create(
            support_user=self.support_user,
            clinic=self.clinic,
            role=ClinicAccess.AccessRole.VIEWER,
            granted_by=self.support_user,
        )
        self.client.force_authenticate(user=self.support_user)

        response = self.client.post(
            '/api/support/tickets/',
            {
                'clinic': str(clinic_b.id),
                'title': 'Ticket forjado para clínica B',
                'description': 'Tentativa de IDOR',
                'priority': 'high',
            },
            format='json',
        )
        self.assertIn(response.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_400_BAD_REQUEST))
        self.assertFalse(Ticket.objects.filter(clinic=clinic_b).exists())

    def test_create_ticket_own_clinic_allowed(self):
        """Support user com ClinicAccess na própria clínica continua criando ticket normalmente via JWT."""
        ClinicAccess.objects.create(
            support_user=self.support_user,
            clinic=self.clinic,
            role=ClinicAccess.AccessRole.VIEWER,
            granted_by=self.support_user,
        )
        self.client.force_authenticate(user=self.support_user)

        response = self.client.post(
            '/api/support/tickets/',
            {
                'clinic': str(self.clinic.id),
                'title': 'Ticket legítimo',
                'description': 'Descrição',
                'priority': 'low',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Ticket.objects.filter(clinic=self.clinic, title='Ticket legítimo').exists())


class TicketMessageAPITest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.clinic = make_clinic()
        self.support_user = SupportUser.objects.create_user(
            username='support', email='s@test.com', password='pass', role='support'
        )
        self.ticket = Ticket.objects.create(
            clinic=self.clinic,
            created_by=self.support_user,
            title='Test',
            description='Test',
        )

    def test_add_message_to_ticket(self):
        """POST /api/support/tickets/:id/messages/ — adicionar mensagem"""
        response = self.client.post(
            f'/api/support/tickets/{self.ticket.id}/messages/',
            {'message': 'Consegui resolver!'},
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.ticket.messages.count(), 1)

    def test_list_messages(self):
        """GET /api/support/tickets/:id/messages/ — listar mensagens"""
        TicketMessage.objects.create(
            ticket=self.ticket,
            author=self.support_user,
            message='Primeira mensagem'
        )

        response = self.client.get(
            f'/api/support/tickets/{self.ticket.id}/messages/',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', response.data)
        self.assertGreaterEqual(len(results), 1)

    def test_cross_tenant_message_access_denied(self):
        """BO-SEC-002: Edge da Clínica A não pode ler mensagens de ticket da Clínica B."""
        clinic_b = make_clinic(name='Clínica B')
        support_user_b = SupportUser.objects.create_user(
            username='support_b', email='b@test.com', password='pass', role='support'
        )
        ticket_b = Ticket.objects.create(
            clinic=clinic_b,
            created_by=support_user_b,
            title='Ticket da clínica B',
            description='Dado sensível da clínica B',
        )
        TicketMessage.objects.create(
            ticket=ticket_b,
            author=support_user_b,
            message='Mensagem confidencial da clínica B',
        )

        # Edge autenticado com license_key da Clínica A tenta ler mensagens do ticket da Clínica B
        response = self.client.get(
            f'/api/support/tickets/{ticket_b.id}/messages/',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        # A view não deve vazar nenhuma mensagem de outro tenant: lista vazia,
        # nunca os dados confidenciais da Clínica B.
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', response.data)
        self.assertEqual(len(results), 0)

    def test_own_clinic_messages_still_accessible(self):
        """Edge da própria clínica continua acessando suas mensagens normalmente."""
        TicketMessage.objects.create(
            ticket=self.ticket,
            author=self.support_user,
            message='Mensagem da própria clínica',
        )

        response = self.client.get(
            f'/api/support/tickets/{self.ticket.id}/messages/',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', response.data)
        self.assertGreaterEqual(len(results), 1)

    def test_cross_tenant_message_create_denied(self):
        """
        BO-SEC-008: Edge da Clínica A não pode POSTar mensagem em ticket da
        Clínica B — o create do TicketMessageCreateSerializer antes confiava
        cegamente no ticket_id da URL, sem checar acesso à clínica do ticket.
        """
        clinic_b = make_clinic(name='Clínica B Create')
        support_user_b = SupportUser.objects.create_user(
            username='support_b_create', email='bc@test.com', password='pass', role='support'
        )
        ticket_b = Ticket.objects.create(
            clinic=clinic_b,
            created_by=support_user_b,
            title='Ticket da clínica B',
            description='Dado sensível da clínica B',
        )

        response = self.client.post(
            f'/api/support/tickets/{ticket_b.id}/messages/',
            {'message': 'Mensagem forjada via IDOR'},
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(ticket_b.messages.count(), 0)