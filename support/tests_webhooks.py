"""
Testes do webhook de VOLTA do Zoho Desk (BACFF-AVULSA-10).

Nunca chama a API real do Zoho — o signal de sync de IDA (sync_ticket_to_zoho)
é desconectado no setUpClass, igual ao padrão já usado em support/tests.py,
pra Ticket.objects.create() não tentar bater na API real do Zoho via Celery
eager.
"""
import json
import uuid

from django.db.models.signals import post_save
from django.test import TestCase, override_settings

from clinics.models import Clinic, ClinicStatus, Plan, ProvisioningStatus
from .models import Ticket, TicketMessage, sync_ticket_to_zoho

WEBHOOK_SECRET = 'test-zoho-webhook-secret-abc123'


def make_clinic(name='Clínica Teste'):
    return Clinic.objects.create(
        name=name,
        slug=f'clinica-{uuid.uuid4().hex[:8]}',
        plan=Plan.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
        db_name=f'clinic_{uuid.uuid4().hex[:8]}',
        db_user=f'u_{uuid.uuid4().hex[:8]}',
        provisioning_status=ProvisioningStatus.PROVISIONED,
    )


@override_settings(ZOHO_DESK_WEBHOOK_SECRET=WEBHOOK_SECRET)
class ZohoTicketCommentWebhookTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        post_save.disconnect(sync_ticket_to_zoho, sender=Ticket)

    @classmethod
    def tearDownClass(cls):
        post_save.connect(sync_ticket_to_zoho, sender=Ticket)
        super().tearDownClass()

    def setUp(self):
        self.clinic = make_clinic()
        self.ticket = Ticket.objects.create(
            clinic=self.clinic,
            title='Problema no app',
            description='Não consigo fazer login',
            status='open',
            priority='medium',
            zoho_ticket_id='zoho-ticket-123',
        )

    def _post_webhook(self, payload, secret=WEBHOOK_SECRET):
        headers = {}
        if secret is not None:
            headers['HTTP_X_WEBHOOK_SECRET'] = secret
        return self.client.post(
            '/api/support/webhooks/zoho-comment/',
            data=json.dumps(payload),
            content_type='application/json',
            **headers,
        )

    def _valid_payload(self, **overrides):
        payload = {
            'ticket_id': 'zoho-ticket-123',
            'comment': 'Já verificamos, tente novamente agora.',
            'author_name': 'Agente Suporte',
        }
        payload.update(overrides)
        return payload

    def test_webhook_authenticated_creates_ticket_message(self):
        """Webhook com secret correto cria TicketMessage vinculado ao Ticket local."""
        response = self._post_webhook(self._valid_payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(TicketMessage.objects.filter(ticket=self.ticket).count(), 1)

        message = TicketMessage.objects.get(ticket=self.ticket)
        self.assertIsNone(message.author)
        self.assertIn('Já verificamos, tente novamente agora.', message.message)
        self.assertIn('Agente Suporte', message.message)

    def test_webhook_missing_secret_rejected(self):
        """Webhook sem header X-Webhook-Secret retorna 401 e não cria mensagem."""
        response = self._post_webhook(self._valid_payload(), secret=None)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(TicketMessage.objects.count(), 0)

    def test_webhook_wrong_secret_rejected(self):
        """Webhook com secret errado retorna 401 e não cria mensagem."""
        response = self._post_webhook(self._valid_payload(), secret='secret-errado')

        self.assertEqual(response.status_code, 401)
        self.assertEqual(TicketMessage.objects.count(), 0)

    def test_webhook_unknown_zoho_ticket_id_returns_200_without_crashing(self):
        """
        Payload autenticado mas com zoho_ticket_id que não bate com nenhum
        Ticket local retorna 200 (evita retry infinito do Zoho) sem criar
        TicketMessage órfão.
        """
        response = self._post_webhook(self._valid_payload(ticket_id='zoho-ticket-nao-existe'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ignored_ticket_not_found')
        self.assertEqual(TicketMessage.objects.count(), 0)

    def test_webhook_missing_comment_field_returns_200_without_crashing(self):
        """Payload incompleto (sem `comment`) não derruba o endpoint e não cria mensagem."""
        payload = self._valid_payload()
        del payload['comment']

        response = self._post_webhook(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(TicketMessage.objects.count(), 0)

    def test_webhook_without_author_name_still_creates_message(self):
        """author_name é opcional — mensagem é criada mesmo sem ele."""
        payload = self._valid_payload()
        del payload['author_name']

        response = self._post_webhook(payload)

        self.assertEqual(response.status_code, 200)
        message = TicketMessage.objects.get(ticket=self.ticket)
        self.assertIn('Já verificamos, tente novamente agora.', message.message)

    def test_webhook_rejects_get_method(self):
        response = self.client.get('/api/support/webhooks/zoho-comment/')
        self.assertEqual(response.status_code, 405)
