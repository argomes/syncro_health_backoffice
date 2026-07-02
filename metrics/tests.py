import uuid
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from clinics.models import Clinic, ClinicStatus, Plan, ProvisioningStatus
from .models import SystemHeartbeat, SystemLog


def make_clinic(name='Clínica Teste'):
    return Clinic.objects.create(
        name=name,
        slug=f'clinica-{uuid.uuid4().hex[:8]}',
        plan=Plan.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj='00.000.000/0001-00',
        db_name=f'clinic_{uuid.uuid4().hex[:8]}',
        provisioning_status=ProvisioningStatus.PROVISIONED,
    )


class HeartbeatTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = '/api/metrics/heartbeat'
        self.clinic = make_clinic()

    def _post(self, payload=None):
        return self.client.post(
            self.url,
            payload or self._default_payload(),
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )

    def _default_payload(self):
        return {
            'gateway_version': '1.2.3',
            'os_info': 'darwin arm64',
            'db_size_mb': 45.2,
            'pending_sync': 5,
            'sync_connected': True,
        }

    def test_first_heartbeat_creates_record(self):
        response = self._post()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(SystemHeartbeat.objects.filter(clinic=self.clinic).count(), 1)

    def test_second_heartbeat_upserts(self):
        self._post()
        payload = self._default_payload()
        payload['pending_sync'] = 99
        self._post(payload)
        self.assertEqual(SystemHeartbeat.objects.filter(clinic=self.clinic).count(), 1)
        hb = SystemHeartbeat.objects.get(clinic=self.clinic)
        self.assertEqual(hb.pending_sync, 99)

    def test_invalid_license_key_returns_4xx(self):
        response = self.client.post(
            self.url,
            self._default_payload(),
            format='json',
            HTTP_X_LICENSE_KEY=str(uuid.uuid4()),  # UUID válido mas não registrado
        )
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_missing_license_key_returns_403(self):
        response = self.client.post(self.url, self._default_payload(), format='json')
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))


class LogsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = '/api/metrics/logs'
        self.clinic = make_clinic()

    def _post(self, logs):
        return self.client.post(
            self.url,
            {'logs': logs},
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )

    def test_bulk_insert_logs(self):
        logs = [
            {'level': 'error', 'message': 'falha PG', 'context': {}, 'occurred_at': '2026-06-28T10:00:00Z'},
            {'level': 'warn', 'message': 'sync lento', 'context': {'latency_ms': 800}, 'occurred_at': '2026-06-28T10:01:00Z'},
        ]
        response = self._post(logs)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['created'], 2)
        self.assertEqual(SystemLog.objects.filter(clinic=self.clinic).count(), 2)

    def test_invalid_occurred_at_is_skipped(self):
        logs = [
            {'level': 'info', 'message': 'ok', 'context': {}, 'occurred_at': 'not-a-date'},
            {'level': 'info', 'message': 'ok2', 'context': {}, 'occurred_at': '2026-06-28T10:00:00Z'},
        ]
        response = self._post(logs)
        self.assertEqual(response.data['created'], 1)

    def test_logs_not_a_list_returns_400(self):
        response = self.client.post(
            self.url,
            {'logs': 'not a list'},
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_license_key_returns_4xx(self):
        response = self.client.post(
            self.url,
            {'logs': []},
            format='json',
            HTTP_X_LICENSE_KEY=str(uuid.uuid4()),
        )
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
