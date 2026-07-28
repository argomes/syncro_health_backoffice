"""
ADMIN-DASHBOARD-REDESIGN — Dashboard de Serviços (home do /admin/).

Cobre: contagem correta por card, isolamento por ClinicAccess (o card não
pode vazar contagem de clínicas fora do escopo do analista — risco nº1
apontado pela revisão de segurança do documento), caso zero, e que o
cache de 60s realmente evita recomputar a query.
"""
import uuid
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import ClinicAccess, SupportUser
from clinics.models import Clinic, ClinicStatus
from clinics.models import Plan as ClinicPlanChoice
from metrics.models import LogLevel, SystemHeartbeat, SystemLog

from .dashboard import dashboard_callback


def _make_clinic(slug, status=ClinicStatus.ACTIVE):
    return Clinic.objects.create(
        name=f'Clínica {slug}', slug=slug,
        plan=ClinicPlanChoice.PROFESSIONAL, status=status,
        cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
        db_name=f'db_{uuid.uuid4().hex[:8]}', db_user=f'u_{uuid.uuid4().hex[:8]}',
    )


class DashboardCallbackCountsTest(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.superuser = SupportUser.objects.create_superuser(
            username='root-dash', email='root-dash@syncro.test', password='x',
        )

    def _request(self, user):
        request = self.factory.get('/admin/')
        request.user = user
        return request

    def test_zero_clinics_gives_zero_counts(self):
        context = dashboard_callback(self._request(self.superuser), {})
        self.assertEqual(context['dashboard_cards']['clinics']['active'], 0)
        self.assertEqual(context['dashboard_cards']['clinics']['inactive'], 0)
        self.assertEqual(context['dashboard_cards']['gateways']['online'], 0)
        self.assertEqual(context['dashboard_cards']['gateways']['total'], 0)
        self.assertEqual(context['dashboard_cards']['errors']['count'], 0)

    def test_counts_active_and_inactive_clinics(self):
        _make_clinic('ativa-1', status=ClinicStatus.ACTIVE)
        _make_clinic('ativa-2', status=ClinicStatus.ACTIVE)
        _make_clinic('suspensa-1', status=ClinicStatus.SUSPENDED)
        _make_clinic('cancelada-1', status=ClinicStatus.CANCELLED)

        context = dashboard_callback(self._request(self.superuser), {})
        cards = context['dashboard_cards']['clinics']
        self.assertEqual(cards['active'], 2)
        self.assertEqual(cards['inactive'], 2)

    def test_gateway_online_cutoff(self):
        clinic = _make_clinic('gateway-1')
        online = SystemHeartbeat.objects.create(clinic=clinic, gateway_version='1.0')
        stale_clinic = _make_clinic('gateway-2')
        stale = SystemHeartbeat.objects.create(clinic=stale_clinic, gateway_version='1.0')
        SystemHeartbeat.objects.filter(pk=stale.pk).update(
            last_seen=timezone.now() - timedelta(hours=1)
        )

        context = dashboard_callback(self._request(self.superuser), {})
        gateways = context['dashboard_cards']['gateways']
        self.assertEqual(gateways['total'], 2)
        self.assertEqual(gateways['online'], 1)

    def test_error_logs_window_24h(self):
        clinic = _make_clinic('erro-1')
        SystemLog.objects.create(
            clinic=clinic, level=LogLevel.ERROR, message='falhou',
            occurred_at=timezone.now(),
        )
        SystemLog.objects.create(
            clinic=clinic, level=LogLevel.ERROR, message='falhou faz tempo',
            occurred_at=timezone.now() - timedelta(hours=48),
        )
        SystemLog.objects.create(
            clinic=clinic, level=LogLevel.INFO, message='tudo ok',
            occurred_at=timezone.now(),
        )

        context = dashboard_callback(self._request(self.superuser), {})
        self.assertEqual(context['dashboard_cards']['errors']['count'], 1)

    def test_scoped_analyst_only_sees_accessible_clinics(self):
        """
        Risco nº1 do documento: o callback roda fora de qualquer ModelAdmin,
        então precisa replicar manualmente a mesma regra do
        TenantScopedAdminMixin. Um analista com ClinicAccess pra 1 de 45
        clínicas deve ver 1, nunca o total global.
        """
        accessible = _make_clinic('acessivel')
        for i in range(5):
            _make_clinic(f'fora-do-escopo-{i}')

        analyst = SupportUser.objects.create_user(
            username='analista-dash', email='analista-dash@syncro.test', password='x',
        )
        ClinicAccess.objects.create(support_user=analyst, clinic=accessible)

        context = dashboard_callback(self._request(analyst), {})
        self.assertEqual(context['dashboard_cards']['clinics']['active'], 1)

    def test_cache_avoids_recomputing_within_ttl(self):
        _make_clinic('cache-1')
        request = self._request(self.superuser)

        dashboard_callback(request, {})
        cache_key = f'admin_dashboard_cards:{self.superuser.pk}'
        cached = cache.get(cache_key)
        self.assertIsNotNone(cached)
        self.assertEqual(cached['clinics']['active'], 1)

        # Cria uma segunda clínica sem invalidar o cache — se o callback
        # recalculasse a cada request, o card mudaria; como está cacheado
        # por 60s, o valor deve continuar o antigo.
        _make_clinic('cache-2')
        context = dashboard_callback(request, {})
        self.assertEqual(context['dashboard_cards']['clinics']['active'], 1)


class DashboardIndexRenderTest(TestCase):
    """Prova end-to-end que os cards aparecem na home do /admin/ e respeitam permissão."""

    def setUp(self):
        cache.clear()
        self.client = Client()

    def test_superuser_sees_all_three_cards(self):
        superuser = SupportUser.objects.create_superuser(
            username='root-dash2', email='root-dash2@syncro.test', password='senha-teste-123',
        )
        self.client.force_login(superuser)

        response = self.client.get(reverse('admin:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Clientes')
        self.assertContains(response, 'Gateways')
        self.assertContains(response, 'Erros (24h)')

    def test_staff_without_permission_does_not_see_gateway_card(self):
        bare_staff = SupportUser.objects.create_user(
            username='dash-sem-perm', email='dash-sem-perm@syncro.test', password='senha-teste-123',
            is_staff=True,
        )
        self.client.force_login(bare_staff)

        response = self.client.get(reverse('admin:index'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Gateways')

    @override_settings(DEBUG=True)
    def test_dashboard_query_count_is_bounded(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        superuser = SupportUser.objects.create_superuser(
            username='root-dash3', email='root-dash3@syncro.test', password='senha-teste-123',
        )
        self.client.force_login(superuser)
        cache.clear()

        # Regressão de performance (documento §6, chamado QA): a home do
        # admin não pode disparar N+1 sobre o volume de clínicas. Um teto
        # generoso — o objetivo é travar crescimento descontrolado, não
        # cravar um número frágil.
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse('admin:index'))
        self.assertEqual(response.status_code, 200)
        self.assertLess(len(ctx.captured_queries), 40)
