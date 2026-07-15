"""
Testes da TASK-049 — tela inicial (dashboard) e seu fragmento HTMX de polling.
"""
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from accounts.models import ClinicUser
from clinics.models import Clinic, ClinicStatus, Plan
from metrics.models import SystemHeartbeat

from . import services
from .models import ReportSessionStatus
from .template_views import SYNC_OFFLINE_THRESHOLD_MINUTES, _sync_state


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


class SyncStateHelperTest(TestCase):
    def test_never_synced(self):
        self.assertEqual(_sync_state({'connected': None, 'last_seen': None}), 'never')

    def test_connected(self):
        self.assertEqual(_sync_state({'connected': True, 'last_seen': timezone.now().isoformat()}), 'ok')

    def test_delayed_within_offline_threshold(self):
        last_seen = (timezone.now() - timedelta(minutes=SYNC_OFFLINE_THRESHOLD_MINUTES - 1)).isoformat()
        self.assertEqual(_sync_state({'connected': False, 'last_seen': last_seen}), 'delayed')

    def test_offline_beyond_threshold(self):
        last_seen = (timezone.now() - timedelta(minutes=SYNC_OFFLINE_THRESHOLD_MINUTES + 1)).isoformat()
        self.assertEqual(_sync_state({'connected': False, 'last_seen': last_seen}), 'offline')

    def test_delayed_at_exact_threshold_boundary(self):
        # <= SYNC_OFFLINE_THRESHOLD_MINUTES ainda é "delayed" (amarelo), não "offline".
        # `timezone.now()` é congelado para o mesmo instante usado em `_sync_state`
        # (que também chama `timezone.now()` internamente) — sem isso, o tempo real
        # decorrido entre as duas chamadas empurra a idade para além do threshold
        # exato de forma intermitente (flake por precisão de clock).
        frozen_now = timezone.now()
        last_seen = (frozen_now - timedelta(minutes=SYNC_OFFLINE_THRESHOLD_MINUTES)).isoformat()
        with patch('portal_gestor.template_views.timezone.now', return_value=frozen_now):
            self.assertEqual(_sync_state({'connected': False, 'last_seen': last_seen}), 'delayed')


class DashboardHomeViewTest(TestCase):
    def setUp(self):
        self.clinic = make_clinic()
        make_clinic_user(self.clinic)
        self.client.post('/portal/login/', {'email': 'gerente@a.com', 'password': 'senha-123'})

    def test_renders_never_synced_state_without_heartbeat(self):
        response = self.client.get('/portal/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Nunca sincronizou')

    def test_renders_synced_state_with_recent_heartbeat(self):
        SystemHeartbeat.objects.create(clinic=self.clinic, gateway_version='1.0.0')
        response = self.client.get('/portal/')
        self.assertContains(response, 'Sincronizado')

    def test_license_card_hidden_when_no_warning(self):
        response = self.client.get('/portal/')
        self.assertNotContains(response, 'Atenção à licença')

    def test_license_card_shown_when_expiring_soon(self):
        self.clinic.license_expires_at = timezone.now() + timedelta(days=5)
        self.clinic.save(update_fields=['license_expires_at'])

        response = self.client.get('/portal/')
        self.assertContains(response, 'Atenção à licença')

    def test_report_shortcut_and_empty_state_render(self):
        response = self.client.get('/portal/')
        self.assertContains(response, 'Gerar Relatório')
        self.assertContains(response, 'Nenhum relatório gerado ainda.')

    def test_recent_report_session_listed(self):
        services.create_report_session(
            clinic=self.clinic, created_by=None, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        response = self.client.get('/portal/')
        self.assertContains(response, 'pending')

    def test_requires_login(self):
        self.client.cookies.clear()
        response = self.client.get('/portal/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/portal/login/'))

    def test_no_cross_tenant_leak_in_rendered_page(self):
        other = make_clinic('Outra Clínica')
        make_clinic_user(other, email='outro@b.com')
        services.create_report_session(
            clinic=other, created_by=None, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )

        response = self.client.get('/portal/')
        self.assertNotContains(response, 'Outra Clínica')


class DashboardFragmentViewTest(TestCase):
    def setUp(self):
        self.clinic = make_clinic()
        make_clinic_user(self.clinic)
        self.client.post('/portal/login/', {'email': 'gerente@a.com', 'password': 'senha-123'})

    def test_fragment_returns_only_cards_not_full_page(self):
        response = self.client.get('/portal/dashboard/fragment/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="dashboard-cards"')
        self.assertNotContains(response, '<header>')  # não é a página inteira

    def test_fragment_requires_login(self):
        self.client.cookies.clear()
        response = self.client.get('/portal/dashboard/fragment/')
        self.assertEqual(response.status_code, 302)

    def test_fragment_reflects_status_change_on_repeated_poll(self):
        first = self.client.get('/portal/dashboard/fragment/')
        self.assertContains(first, 'Nunca sincronizou')

        SystemHeartbeat.objects.create(clinic=self.clinic, gateway_version='1.0.0')

        second = self.client.get('/portal/dashboard/fragment/')
        self.assertContains(second, 'Sincronizado')

    def test_fragment_no_cross_tenant_leak(self):
        # Mesma checagem de isolamento feita para DashboardHomeViewTest, mas
        # explicitamente contra o endpoint de polling HTMX — ele é o que fica
        # rodando em loop a cada 30s, então uma regressão de escopo aqui
        # vazaria dados de outra clínica de forma contínua e silenciosa.
        other = make_clinic('Outra Clínica')
        make_clinic_user(other, email='outro@b.com')
        services.create_report_session(
            clinic=other, created_by=None, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )

        response = self.client.get('/portal/dashboard/fragment/')
        self.assertNotContains(response, 'Outra Clínica')
