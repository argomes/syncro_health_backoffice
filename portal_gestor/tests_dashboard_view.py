"""
Testes da TASK-049 — tela inicial (dashboard) e seu fragmento HTMX de polling.
"""
import uuid
from datetime import timedelta

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from accounts.models import ClinicUser
from clinics.models import Clinic, ClinicStatus, Plan
from metrics.models import SystemHeartbeat

from . import services
from .models import ReportSessionStatus
from .dashboard import (
    HEARTBEAT_STALE_THRESHOLD_MINUTES,
    SYNC_PENDING_TOLERANCE,
    SYNC_STATE_NEVER,
    SYNC_STATE_OFFLINE,
    SYNC_STATE_OK,
    SYNC_STATE_SYNCING,
    compute_sync_status,
    humanize_age_ptbr,
)


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


class ComputeSyncStatusTest(SimpleTestCase):
    """O1 — regra honesta do card de sincronização (função pura, sem banco)."""

    def setUp(self):
        self.now = timezone.now()

    def _heartbeat(self, minutes_ago=1, sync_connected=True, pending_sync=0):
        # Instância não salva: compute_sync_status só lê atributos, então o
        # teste fica determinístico (sem auto_now sobrescrevendo last_seen).
        return SystemHeartbeat(
            gateway_version='1.0.0',
            last_seen=self.now - timedelta(minutes=minutes_ago),
            sync_connected=sync_connected,
            pending_sync=pending_sync,
        )

    def test_never_synced_is_gray(self):
        status = compute_sync_status(None, self.now)
        self.assertEqual(status.state, SYNC_STATE_NEVER)
        self.assertEqual(status.badge, 'gray')
        self.assertIsNone(status.last_seen)

    def test_green_when_recent_connected_and_no_backlog(self):
        status = compute_sync_status(self._heartbeat(minutes_ago=3, pending_sync=0), self.now)
        self.assertEqual(status.state, SYNC_STATE_OK)
        self.assertEqual(status.badge, 'green')
        self.assertEqual(status.label, 'Sincronizado')
        self.assertEqual(status.last_seen_ago, 'há 3 min')

    def test_green_tolerates_small_in_flight_backlog(self):
        status = compute_sync_status(self._heartbeat(pending_sync=SYNC_PENDING_TOLERANCE), self.now)
        self.assertEqual(status.state, SYNC_STATE_OK)

    def test_yellow_when_connected_with_backlog_above_tolerance(self):
        pending = SYNC_PENDING_TOLERANCE + 7
        status = compute_sync_status(self._heartbeat(pending_sync=pending), self.now)
        self.assertEqual(status.state, SYNC_STATE_SYNCING)
        self.assertEqual(status.badge, 'yellow')
        self.assertEqual(status.label, f'Sincronizando — {pending} pendentes')
        self.assertEqual(status.pending_sync, pending)

    def test_red_when_recent_heartbeat_but_cloud_disconnected(self):
        # Era o bug do O1: heartbeat recente deixava verde mesmo sem nuvem.
        status = compute_sync_status(self._heartbeat(minutes_ago=1, sync_connected=False), self.now)
        self.assertEqual(status.state, SYNC_STATE_OFFLINE)
        self.assertEqual(status.badge, 'red')
        self.assertEqual(status.label, 'Sem conexão com a nuvem')

    def test_red_when_heartbeat_stale_even_if_last_report_was_connected(self):
        hb = self._heartbeat(minutes_ago=HEARTBEAT_STALE_THRESHOLD_MINUTES + 1, sync_connected=True)
        status = compute_sync_status(hb, self.now)
        self.assertEqual(status.state, SYNC_STATE_OFFLINE)

    def test_stale_heartbeat_with_backlog_is_red_not_yellow(self):
        hb = self._heartbeat(minutes_ago=HEARTBEAT_STALE_THRESHOLD_MINUTES + 1, pending_sync=500)
        self.assertEqual(compute_sync_status(hb, self.now).state, SYNC_STATE_OFFLINE)

    def test_exact_stale_threshold_is_still_fresh(self):
        hb = self._heartbeat(minutes_ago=HEARTBEAT_STALE_THRESHOLD_MINUTES)
        self.assertEqual(compute_sync_status(hb, self.now).state, SYNC_STATE_OK)

    def test_stale_threshold_is_ten_minutes_by_default(self):
        self.assertEqual(HEARTBEAT_STALE_THRESHOLD_MINUTES, 10)


class HumanizeAgePtBrTest(SimpleTestCase):
    def test_formats(self):
        self.assertEqual(humanize_age_ptbr(timedelta(seconds=-5)), 'agora mesmo')
        self.assertEqual(humanize_age_ptbr(timedelta(seconds=30)), 'agora mesmo')
        self.assertEqual(humanize_age_ptbr(timedelta(minutes=1)), 'há 1 min')
        self.assertEqual(humanize_age_ptbr(timedelta(minutes=59)), 'há 59 min')
        self.assertEqual(humanize_age_ptbr(timedelta(hours=2, minutes=10)), 'há 2 h')
        self.assertEqual(humanize_age_ptbr(timedelta(days=1, hours=3)), 'há 1 dia')
        self.assertEqual(humanize_age_ptbr(timedelta(days=4)), 'há 4 dias')


class DashboardHomeViewTest(TestCase):
    def setUp(self):
        self.clinic = make_clinic()
        make_clinic_user(self.clinic)
        self.client.post('/portal/login/', {'email': 'gerente@a.com', 'password': 'senha-123'})

    def test_renders_never_synced_state_without_heartbeat(self):
        response = self.client.get('/portal/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Nunca sincronizou')

    def test_renders_green_synced_state(self):
        SystemHeartbeat.objects.create(clinic=self.clinic, gateway_version='1.0.0', sync_connected=True)
        response = self.client.get('/portal/')
        self.assertContains(response, 'badge-green')
        self.assertContains(response, 'Sincronizado')
        self.assertContains(response, 'Última sincronização: agora mesmo')
        self.assertContains(response, timezone.localtime().strftime('%d/%m/%Y'))
        self.assertNotContains(response, 'Sem conexão com a nuvem')

    def test_renders_yellow_syncing_state_with_backlog(self):
        SystemHeartbeat.objects.create(
            clinic=self.clinic, gateway_version='1.0.0', sync_connected=True,
            pending_sync=SYNC_PENDING_TOLERANCE + 20,
        )
        response = self.client.get('/portal/')
        self.assertContains(response, 'badge-yellow')
        self.assertContains(response, f'Sincronizando — {SYNC_PENDING_TOLERANCE + 20} pendentes')

    def test_renders_red_when_heartbeat_recent_but_cloud_disconnected(self):
        SystemHeartbeat.objects.create(clinic=self.clinic, gateway_version='1.0.0', sync_connected=False)
        response = self.client.get('/portal/')
        self.assertContains(response, 'badge-red')
        self.assertContains(response, 'Sem conexão com a nuvem')
        self.assertNotContains(response, '● Sincronizado')

    def test_renders_red_when_heartbeat_stale(self):
        hb = SystemHeartbeat.objects.create(clinic=self.clinic, gateway_version='1.0.0', sync_connected=True)
        stale = timezone.now() - timedelta(minutes=HEARTBEAT_STALE_THRESHOLD_MINUTES + 25)
        SystemHeartbeat.objects.filter(pk=hb.pk).update(last_seen=stale)
        response = self.client.get('/portal/')
        self.assertContains(response, 'Sem conexão com a nuvem')
        self.assertContains(response, 'Última sincronização: há 35 min')
        self.assertContains(response, timezone.localtime(stale).strftime('%d/%m/%Y %H:%M'))

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

    def test_recent_report_session_listed_with_ptbr_status_and_dates(self):
        # O2: antes o card mostrava "pending" cru e datas vazias (|date sobre
        # string ISO vinda do serializer).
        date_from = timezone.now() - timedelta(days=3)
        date_to = timezone.now() - timedelta(days=1)
        services.create_report_session(
            clinic=self.clinic, created_by=None, entities=['patients'],
            date_from=date_from, date_to=date_to,
        )
        response = self.client.get('/portal/')
        self.assertContains(response, 'Pendente')
        self.assertNotContains(response, '>pending<')
        self.assertContains(response, timezone.localtime(date_from).strftime('%d/%m/%Y'))
        self.assertContains(response, timezone.localtime(date_to).strftime('%d/%m/%Y'))
        self.assertContains(response, 'pedido em')

    def test_expired_session_status_in_ptbr(self):
        session = services.create_report_session(
            clinic=self.clinic, created_by=None, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        session.mark_expired()
        response = self.client.get('/portal/')
        self.assertContains(response, 'Expirado')
        self.assertNotContains(response, '>expired<')

    def test_suspended_clinic_status_shown_in_ptbr(self):
        self.clinic.status = ClinicStatus.SUSPENDED
        self.clinic.save(update_fields=['status'])
        response = self.client.get('/portal/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Suspensa')
        self.assertNotContains(response, '>suspended<')

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

        SystemHeartbeat.objects.create(clinic=self.clinic, gateway_version='1.0.0', sync_connected=True)

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
