"""
Testes de BACFF-AVULSA-06 — endpoint POST /api/clinics/db-access-grant/.

Cobre: grant válido grava no cache com o formato esperado, TTL default e
teto de 4h, rejeição de payload inválido, e guard de clínica não provisionada.
"""
import uuid

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from .models import Clinic, ClinicStatus, Plan, ProvisioningStatus
from .views import DB_ACCESS_GRANT_DEFAULT_TTL_SECONDS, DB_ACCESS_GRANT_MAX_TTL_SECONDS


def make_clinic(clinic_status=ClinicStatus.ACTIVE, provisioning_status=ProvisioningStatus.PROVISIONED, **kwargs):
    return Clinic.objects.create(
        name='Clínica Teste',
        slug=f'clinica-{uuid.uuid4().hex[:8]}',
        plan=Plan.PROFESSIONAL,
        status=clinic_status,
        cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
        db_name=f'clinic_{uuid.uuid4().hex[:8]}',
        db_user=f'u_{uuid.uuid4().hex[:8]}',
        provisioning_status=provisioning_status,
        **kwargs,
    )


class DbAccessGrantTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.url = '/api/clinics/db-access-grant/'

    def test_valid_grant_returns_200_and_writes_cache(self):
        clinic = make_clinic()
        response = self.client.post(
            self.url,
            {'db_password': 's3nh4-temp', 'ttl_seconds': 3600, 'granted_by': 'admin-local'},
            format='json',
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertTrue(data['granted'])
        self.assertIn('expires_at', data)

        cached = cache.get(f"clinic_db_grant:{clinic.id}")
        self.assertIsNotNone(cached)
        self.assertEqual(cached['password'], 's3nh4-temp')
        self.assertEqual(cached['granted_by'], 'admin-local')
        self.assertIn('granted_at', cached)
        self.assertIn('expires_at', cached)

    def test_omitted_ttl_uses_default_4h(self):
        clinic = make_clinic()
        response = self.client.post(
            self.url,
            {'db_password': 's3nh4-temp', 'granted_by': 'admin-local'},
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(cache.get(f"clinic_db_grant:{clinic.id}"))
        self.assertEqual(DB_ACCESS_GRANT_DEFAULT_TTL_SECONDS, 14400)

    def test_ttl_above_max_rejected_with_400(self):
        clinic = make_clinic()
        response = self.client.post(
            self.url,
            {'db_password': 's3nh4-temp', 'ttl_seconds': DB_ACCESS_GRANT_MAX_TTL_SECONDS + 1, 'granted_by': 'x'},
            format='json',
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()['error'], 'ttl_seconds_invalid')
        self.assertIsNone(cache.get(f"clinic_db_grant:{clinic.id}"))

    def test_negative_or_zero_ttl_rejected(self):
        clinic = make_clinic()
        response = self.client.post(
            self.url,
            {'db_password': 's3nh4-temp', 'ttl_seconds': 0, 'granted_by': 'x'},
            format='json',
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_password_rejected_with_400(self):
        clinic = make_clinic()
        response = self.client.post(
            self.url,
            {'granted_by': 'admin-local'},
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()['error'], 'db_password_required')

    def test_blank_password_rejected_with_400(self):
        clinic = make_clinic()
        response = self.client.post(
            self.url,
            {'db_password': '   ', 'granted_by': 'admin-local'},
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_not_provisioned_clinic_returns_424(self):
        clinic = make_clinic(provisioning_status=ProvisioningStatus.PENDING)
        response = self.client.post(
            self.url,
            {'db_password': 's3nh4-temp', 'granted_by': 'admin-local'},
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_424_FAILED_DEPENDENCY)
        self.assertEqual(response.json()['error'], 'not_provisioned')

    def test_missing_license_key_returns_401(self):
        response = self.client.post(self.url, {'db_password': 'x', 'granted_by': 'y'})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_new_grant_overwrites_previous(self):
        clinic = make_clinic()
        self.client.post(
            self.url,
            {'db_password': 'senha-antiga', 'granted_by': 'admin-1'},
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )
        self.client.post(
            self.url,
            {'db_password': 'senha-nova', 'granted_by': 'admin-2'},
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )
        cached = cache.get(f"clinic_db_grant:{clinic.id}")
        self.assertEqual(cached['password'], 'senha-nova')
        self.assertEqual(cached['granted_by'], 'admin-2')


class DbAccessGrantThrottleTests(TestCase):
    """
    Regressão da correção de BACFF-AVULSA-06 (2026-07-17): db-access-grant
    grava uma senha de banco Postgres válida em cache e, por depender só de
    IsAuthenticatedByLicenseKey (permission, não authentication), o DRF
    tratava a requisição como anônima e herdava apenas o AnonRateThrottle
    default (60/min) — insuficiente. Agora usa um throttle dedicado
    (DbAccessGrantRateThrottle, scope='db_access_grant').

    NOTA: `SimpleRateThrottle.THROTTLE_RATES` é lido de `api_settings` como
    atributo de CLASSE, no momento em que o módulo `rest_framework.throttling`
    é importado — portanto `override_settings(REST_FRAMEWORK=...)` em tempo
    de teste não tem efeito sobre throttles já carregados. Por isso os testes
    abaixo exercitam a rate real de produção (settings.py), lida
    dinamicamente, em vez de uma rate reduzida artificialmente para o teste.
    """

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.url = '/api/clinics/db-access-grant/'
        rate = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['db_access_grant']
        num_requests, period = rate.split('/')
        assert period == 'hour', "teste assume rate por hora; ajustar se a unidade mudar"
        self.limit = int(num_requests)

    def _grant(self, clinic):
        return self.client.post(
            self.url,
            {'db_password': 's3nh4-temp', 'granted_by': 'admin-local'},
            format='json',
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )

    def test_calls_within_limit_succeed(self):
        clinic = make_clinic()
        for _ in range(self.limit):
            response = self._grant(clinic)
            self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_call_beyond_limit_returns_429(self):
        clinic = make_clinic()
        for _ in range(self.limit):
            response = self._grant(clinic)
            self.assertEqual(response.status_code, status.HTTP_200_OK)

        # (limit + 1)-ésima chamada no período estoura a rate de produção.
        response = self._grant(clinic)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_throttle_is_per_ip_not_per_clinic(self):
        # O throttle usa a permission (não authentication), então a chave de
        # rate limit do DRF é baseada em IP, não em clínica — duas clínicas
        # diferentes batendo do mesmo IP compartilham o mesmo balde.
        clinic_a = make_clinic()
        clinic_b = make_clinic()
        for _ in range(self.limit):
            self.assertEqual(self._grant(clinic_a).status_code, status.HTTP_200_OK)

        response = self._grant(clinic_b)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class DbAccessGrantProductionThrottleRateTests(TestCase):
    def test_production_rate_is_low_and_hourly(self):
        rate = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['db_access_grant']
        num, period = rate.split('/')
        self.assertLessEqual(int(num), 10)
        self.assertGreaterEqual(int(num), 5)
        self.assertEqual(period, 'hour')
