"""
Testes da TASK-048 — endpoint de dashboard consolidado.

Cobre os 3 blocos do payload isoladamente (gateway_status, license,
report_sessions), casos-limite (clínica sem heartbeat, licença sem
expires_at, limite exato de dias de aviso), o payload combinado via HTTP, e
isolamento entre clínicas.
"""
import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import ClinicUser
from clinics.models import Clinic, ClinicStatus, Plan
from metrics.models import SystemHeartbeat

from . import dashboard, services
from .models import ReportSessionStatus


def make_clinic(name='Clínica Teste', **kwargs):
    return Clinic.objects.create(
        name=name,
        slug=f'clinica-{uuid.uuid4().hex[:8]}',
        plan=Plan.PROFESSIONAL,
        status=kwargs.pop('status', ClinicStatus.ACTIVE),
        cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
        db_name=f'clinic_{uuid.uuid4().hex[:8]}',
        db_user=f'u_{uuid.uuid4().hex[:8]}',
        **kwargs,
    )


def make_clinic_user(clinic, email='gerente@a.com', password='senha-123'):
    user = ClinicUser(clinic=clinic, email=email, name='Gerente')
    user.set_password(password)
    user.save()
    return user


class GatewayStatusTest(TestCase):
    def setUp(self):
        self.clinic = make_clinic()

    def test_no_heartbeat_yet_returns_null_connected(self):
        result = dashboard._gateway_status(self.clinic)
        self.assertIsNone(result['connected'])
        self.assertIsNone(result['last_seen'])

    def test_recent_heartbeat_is_connected(self):
        SystemHeartbeat.objects.create(clinic=self.clinic, gateway_version='1.0.0', pending_sync=2)
        result = dashboard._gateway_status(self.clinic)
        self.assertTrue(result['connected'])
        self.assertEqual(result['pending_sync'], 2)
        self.assertEqual(result['gateway_version'], '1.0.0')

    def test_stale_heartbeat_is_not_connected(self):
        hb = SystemHeartbeat.objects.create(clinic=self.clinic, gateway_version='1.0.0')
        stale = timezone.now() - timedelta(minutes=dashboard.HEARTBEAT_STALE_THRESHOLD_MINUTES + 1)
        SystemHeartbeat.objects.filter(pk=hb.pk).update(last_seen=stale)

        result = dashboard._gateway_status(self.clinic)
        self.assertFalse(result['connected'])

    def test_heartbeat_just_inside_threshold_boundary_is_still_connected(self):
        # Um segundo de folga em vez do limite exato: comparar contra o exato
        # limiar é inerentemente instável em wall-clock (o tempo decorrido
        # entre montar `boundary` e chamar _gateway_status já é > 0).
        hb = SystemHeartbeat.objects.create(clinic=self.clinic, gateway_version='1.0.0')
        just_inside = timezone.now() - timedelta(minutes=dashboard.HEARTBEAT_STALE_THRESHOLD_MINUTES) + timedelta(seconds=5)
        SystemHeartbeat.objects.filter(pk=hb.pk).update(last_seen=just_inside)

        result = dashboard._gateway_status(self.clinic)
        self.assertTrue(result['connected'])


class LicenseStatusTest(TestCase):
    def test_no_expiry_date_no_warning_when_active(self):
        clinic = make_clinic()
        result = dashboard._license_status(clinic)
        self.assertIsNone(result['days_until_expiry'])
        self.assertFalse(result['warning'])

    def test_far_future_expiry_no_warning(self):
        clinic = make_clinic(license_expires_at=timezone.now() + timedelta(days=365))
        result = dashboard._license_status(clinic)
        self.assertFalse(result['warning'])

    def test_within_warning_window_triggers_warning(self):
        clinic = make_clinic(license_expires_at=timezone.now() + timedelta(days=dashboard.LICENSE_WARNING_DAYS - 1))
        result = dashboard._license_status(clinic)
        self.assertTrue(result['warning'])

    def test_exact_warning_boundary_triggers_warning(self):
        clinic = make_clinic(license_expires_at=timezone.now() + timedelta(days=dashboard.LICENSE_WARNING_DAYS))
        result = dashboard._license_status(clinic)
        self.assertTrue(result['warning'])

    def test_already_expired_triggers_warning_with_negative_days(self):
        clinic = make_clinic(license_expires_at=timezone.now() - timedelta(days=5))
        result = dashboard._license_status(clinic)
        self.assertTrue(result['warning'])
        self.assertLess(result['days_until_expiry'], 0)

    def test_suspended_clinic_always_warns_regardless_of_expiry(self):
        clinic = make_clinic(status=ClinicStatus.SUSPENDED, license_expires_at=timezone.now() + timedelta(days=365))
        result = dashboard._license_status(clinic)
        self.assertTrue(result['warning'])


class ReportSessionsSummaryTest(TestCase):
    def setUp(self):
        self.clinic = make_clinic()
        self.user = make_clinic_user(self.clinic)

    def test_no_sessions_returns_empty(self):
        result = dashboard._report_sessions_summary(self.clinic)
        self.assertEqual(result['recent'], [])
        self.assertEqual(result['active_count'], 0)

    def test_active_and_finished_sessions_counted_correctly(self):
        active = services.create_report_session(
            clinic=self.clinic, created_by=self.user, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        active.status = ReportSessionStatus.KEY_DELIVERED
        active.save(update_fields=['status'])

        finished = services.create_report_session(
            clinic=self.clinic, created_by=self.user, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        finished.status = ReportSessionStatus.READY
        finished.save(update_fields=['status'])

        expired = services.create_report_session(
            clinic=self.clinic, created_by=self.user, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        expired.status = ReportSessionStatus.PENDING
        expired.expires_at = timezone.now() - timedelta(seconds=1)
        expired.save(update_fields=['status', 'expires_at'])

        result = dashboard._report_sessions_summary(self.clinic)
        self.assertEqual(result['active_count'], 1)  # só "active" (pending expirou, não conta)
        self.assertEqual(len(result['recent']), 3)

    def test_other_clinic_sessions_not_counted(self):
        other_clinic = make_clinic('Outra Clínica')
        other_user = make_clinic_user(other_clinic, email='outro@b.com')
        services.create_report_session(
            clinic=other_clinic, created_by=other_user, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )

        result = dashboard._report_sessions_summary(self.clinic)
        self.assertEqual(result['recent'], [])
        self.assertEqual(result['active_count'], 0)


class DashboardSummaryEndpointTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.clinic_a = make_clinic('Clínica A')
        self.clinic_b = make_clinic('Clínica B')
        self.user_a = make_clinic_user(self.clinic_a, email='gerente@a.com')
        self.user_b = make_clinic_user(self.clinic_b, email='gerente@b.com')

    def _login_bearer(self, email='gerente@a.com', password='senha-123'):
        resp = self.client.post('/portal/api/auth/login/', {'email': email, 'password': password}, format='json')
        return {'HTTP_AUTHORIZATION': f'Bearer {resp.data["access"]}'}

    def test_requires_auth(self):
        response = self.client.get('/portal/api/dashboard/summary/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_returns_combined_payload_via_bearer_token(self):
        auth = self._login_bearer()
        response = self.client.get('/portal/api/dashboard/summary/', **auth)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('gateway_status', response.data)
        self.assertIn('license', response.data)
        self.assertIn('report_sessions', response.data)

    def test_works_via_cookie_too(self):
        self.client.post('/portal/login/', {'email': 'gerente@a.com', 'password': 'senha-123'})
        response = self.client.get('/portal/api/dashboard/summary/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_scoped_to_own_clinic_no_cross_tenant_leak(self):
        SystemHeartbeat.objects.create(clinic=self.clinic_b, gateway_version='9.9.9')

        auth = self._login_bearer()
        response = self.client.get('/portal/api/dashboard/summary/', **auth)

        self.assertIsNone(response.data['gateway_status']['gateway_version'])

    def test_expired_or_garbage_cookie_falls_back_to_valid_bearer_token(self):
        # Cenário real: cookie de sessão do portal HTML expirou (ou está corrompido),
        # mas o request também traz um Authorization: Bearer válido (ex.: SPA/API
        # consumer). Como authentication_classes=[Cookie, Bearer], o cookie inválido
        # NÃO pode abortar a cadeia antes do bearer ser tentado — ver
        # accounts/authentication.py::ClinicCookieJWTAuthentication.authenticate.
        from accounts.authentication import PORTAL_ACCESS_COOKIE

        auth = self._login_bearer()
        self.client.cookies[PORTAL_ACCESS_COOKIE] = 'garbage-not-a-real-jwt'

        response = self.client.get('/portal/api/dashboard/summary/', **auth)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_garbage_cookie_alone_without_bearer_is_unauthenticated(self):
        # Garante que o fallback não vira um "sempre autentica": sem bearer
        # válido, um cookie inválido continua resultando em 401 (não 500 nem
        # um bypass silencioso).
        from accounts.authentication import PORTAL_ACCESS_COOKIE

        self.client.cookies[PORTAL_ACCESS_COOKIE] = 'garbage-not-a-real-jwt'
        response = self.client.get('/portal/api/dashboard/summary/')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
