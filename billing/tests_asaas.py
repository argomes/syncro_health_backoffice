import json
from unittest.mock import patch
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from clinics.models import Clinic, ClinicStatus, Plan
from billing.asaas import AsaasClient

WEBHOOK_TOKEN = 'test-webhook-token-abc123'


class AsaasIntegrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.clinic = Clinic.objects.create(
            name='Clínica ASAAS Test',
            slug='clinic-asaas',
            plan=Plan.PROFESSIONAL,
            status=ClinicStatus.SUSPENDED,  # Começa suspensa
            active_modules=['dental'],
            gateway_url='http://localhost:8080',
            asaas_customer_id='cus_12345',
            asaas_subscription_id='sub_12345',
        )

    def _post_webhook(self, payload, token=WEBHOOK_TOKEN):
        """Helper que envia payload com o header de autenticação correto."""
        return self.client.post(
            '/api/v1/billing/webhook/asaas/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_ASAAS_ACCESS_TOKEN=token,
        )

    def test_asaas_client_mock_methods(self):
        """Testa se o AsaasClient entra em modo mock e retorna IDs fakes corretos."""
        client = AsaasClient()
        self.assertTrue(client.is_mock)

        cust_id = client.create_customer('Teste Name', '12.345.678/0001-99')
        self.assertEqual(cust_id, 'cus_mock_12345678000199')

        sub_id = client.create_subscription(cust_id, 299.99)
        self.assertEqual(sub_id, f'sub_mock_{cust_id}')

    @override_settings(ASAAS_WEBHOOK_TOKEN=WEBHOOK_TOKEN)
    def test_webhook_rejects_missing_token(self):
        """Webhook sem header asaas-access-token retorna 401."""
        payload = {'event': 'PAYMENT_RECEIVED', 'payment': {}}
        response = self.client.post(
            '/api/v1/billing/webhook/asaas/',
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)

    @override_settings(ASAAS_WEBHOOK_TOKEN=WEBHOOK_TOKEN)
    def test_webhook_rejects_wrong_token(self):
        """Webhook com token errado retorna 401."""
        payload = {'event': 'PAYMENT_RECEIVED', 'payment': {}}
        response = self._post_webhook(payload, token='wrong-token')
        self.assertEqual(response.status_code, 401)

    @override_settings(ASAAS_WEBHOOK_TOKEN=WEBHOOK_TOKEN)
    @patch('clinics.signals.sync_modules_to_edge')
    def test_webhook_payment_received_activates_clinic(self, mock_task):
        """Confirmação de pagamento ativa a clínica e enfileira sync dos módulos."""
        payload = {
            'event': 'PAYMENT_RECEIVED',
            'payment': {
                'subscription': 'sub_12345',
                'customer': 'cus_12345',
            }
        }
        response = self._post_webhook(payload)
        self.assertEqual(response.status_code, 200)

        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.status, ClinicStatus.ACTIVE)
        mock_task.delay.assert_called_once_with(str(self.clinic.pk))

    @override_settings(ASAAS_WEBHOOK_TOKEN=WEBHOOK_TOKEN)
    @patch('clinics.signals.sync_modules_to_edge')
    def test_webhook_payment_overdue_suspends_clinic(self, mock_task):
        """Inadimplência suspende a clínica e enfileira sync (que enviará lista vazia)."""
        # Define como ativa primeiro
        self.clinic.status = ClinicStatus.ACTIVE
        self.clinic.save()
        mock_task.reset_mock()

        payload = {
            'event': 'PAYMENT_OVERDUE',
            'payment': {
                'subscription': 'sub_12345',
                'customer': 'cus_12345',
            }
        }
        response = self._post_webhook(payload)
        self.assertEqual(response.status_code, 200)

        self.clinic.refresh_from_db()
        self.assertEqual(self.clinic.status, ClinicStatus.SUSPENDED)
        mock_task.delay.assert_called_once_with(str(self.clinic.pk))

    @override_settings(ASAAS_WEBHOOK_TOKEN=WEBHOOK_TOKEN)
    def test_webhook_clinic_not_found(self):
        """Webhook com dados não correspondentes retorna 200 de forma segura."""
        payload = {
            'event': 'PAYMENT_RECEIVED',
            'payment': {
                'subscription': 'sub_unknown',
                'customer': 'cus_unknown',
            }
        }
        response = self._post_webhook(payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ignored_clinic_not_found')
