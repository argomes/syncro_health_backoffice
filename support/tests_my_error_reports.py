from django.db.models.signals import post_save
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from .models import Ticket, sync_ticket_to_zoho
from .tests import make_clinic


class MyErrorReportsAPITest(TestCase):
    """
    EDGW-067 — GET /api/support/error-reports/mine/?reporter_user_id=...

    Visão "Meus Chamados": cada reporter (identificado por reporter_user_id,
    o `sub` do JWT local do gateway) vê só os tickets que ele mesmo abriu
    a partir do app desktop, nunca o board completo da clínica.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        post_save.disconnect(sync_ticket_to_zoho, sender=Ticket)

    def setUp(self):
        self.client = APIClient()
        self.clinic = make_clinic()
        self.url = '/api/support/error-reports/mine/'

    def _make_ticket(self, clinic=None, reporter_user_id='user-a', **overrides):
        defaults = dict(
            clinic=clinic or self.clinic,
            title='[Erro Desktop] teste',
            description='descrição',
            priority='high',
            reporter_user_id=reporter_user_id,
        )
        defaults.update(overrides)
        return Ticket.objects.create(**defaults)

    def test_without_license_key_returns_401(self):
        response = self.client.get(self.url, {'reporter_user_id': 'user-a'})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_missing_reporter_user_id_returns_empty_list_not_full_board(self):
        self._make_ticket(reporter_user_id='user-a')
        self._make_ticket(reporter_user_id='user-b')
        response = self.client.get(
            self.url, HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_returns_only_own_tickets(self):
        mine = self._make_ticket(reporter_user_id='user-a')
        self._make_ticket(reporter_user_id='user-b')

        response = self.client.get(
            self.url,
            {'reporter_user_id': 'user-a'},
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], mine.id)

    def test_does_not_expose_description(self):
        self._make_ticket(reporter_user_id='user-a', description='PHI sensível não pode vazar')
        response = self.client.get(
            self.url,
            {'reporter_user_id': 'user-a'},
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertNotIn('description', response.data[0])
        self.assertEqual(
            set(response.data[0].keys()),
            {'id', 'title', 'status', 'priority', 'created_at', 'resolved_at'},
        )

    def test_isolated_across_clinics_even_with_same_reporter_user_id(self):
        # Mesmo reporter_user_id (colisão de hash, teoricamente possível) em
        # clínicas diferentes nunca deve vazar cross-tenant.
        other_clinic = make_clinic(name='Outra Clínica')
        self._make_ticket(clinic=self.clinic, reporter_user_id='same-id')
        self._make_ticket(clinic=other_clinic, reporter_user_id='same-id')

        response = self.client.get(
            self.url,
            {'reporter_user_id': 'same-id'},
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(len(response.data), 1)

    def test_reporter_user_id_persisted_from_error_report_creation(self):
        payload = {
            'category': 'sincronizacao',
            'description': 'Falha ao sincronizar.',
            'severity': 'alto',
            'reporter_user_id': 'user-xyz',
        }
        response = self.client.post(
            '/api/support/error-reports/',
            payload,
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        ticket = Ticket.objects.get(id=response.data['ticket_id'])
        self.assertEqual(ticket.reporter_user_id, 'user-xyz')

        list_response = self.client.get(
            self.url,
            {'reporter_user_id': 'user-xyz'},
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(len(list_response.data), 1)
        self.assertEqual(list_response.data[0]['id'], ticket.id)
