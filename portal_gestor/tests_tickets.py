"""
BACFF-AVULSA-09 — chamados de suporte visíveis pelo admin da clínica no
portal_gestor (API + páginas server-rendered) e toggle de
`Clinic.support_ticket_restricted_to_admin`.

Cobre: isolamento cross-tenant (clínica A nunca vê ticket de clínica B),
exigência de autenticação, parsing de categoria (sem duplicar a lógica de
support/serializers.py::ErrorReportCreateSerializer) e persistência do toggle.
"""
import uuid

from django.core.cache import cache
from django.db.models.signals import post_save
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import ClinicUser
from clinics.models import Clinic, ClinicStatus, Plan
from portal_gestor.models import PortalReadAuditLog
from support.models import Ticket, TicketMessage, sync_ticket_to_zoho


def make_clinic(name='Clínica Teste'):
    return Clinic.objects.create(
        name=name,
        slug=f'clinica-{uuid.uuid4().hex[:8]}',
        plan=Plan.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
        db_name=f'clinic_{uuid.uuid4().hex[:8]}',
        db_user=f'u_{uuid.uuid4().hex[:8]}',
    )


def make_clinic_user(clinic, email='gerente@a.com', password='senha-123'):
    user = ClinicUser(clinic=clinic, email=email, name='Gerente')
    user.set_password(password)
    user.save()
    return user


class TicketApiViewsTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Evita chamada real ao Zoho Desk durante os testes (mesmo cuidado de
        # support/tests.py — CELERY_TASK_ALWAYS_EAGER=True localmente).
        post_save.disconnect(sync_ticket_to_zoho, sender=Ticket)

    @classmethod
    def tearDownClass(cls):
        post_save.connect(sync_ticket_to_zoho, sender=Ticket)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.clinic_a = make_clinic('Clínica A')
        self.clinic_b = make_clinic('Clínica B')
        self.user_a = make_clinic_user(self.clinic_a, email='gerente@a.com')
        self.user_b = make_clinic_user(self.clinic_b, email='gerente@b.com')

        self.ticket_a = Ticket.objects.create(
            clinic=self.clinic_a,
            title='[Erro Desktop] sincronizacao',
            description='[Origem: App Desktop] [Categoria: sincronizacao]\n[Role: recepcao]\n\nFalhou.',
            priority='high',
        )
        self.ticket_b = Ticket.objects.create(
            clinic=self.clinic_b,
            title='Outro chamado',
            description='descrição de outra clínica',
            priority='low',
        )

    def _login(self, email, password='senha-123'):
        resp = self.client.post('/portal/api/auth/login/', {'email': email, 'password': password}, format='json')
        return {'HTTP_AUTHORIZATION': f'Bearer {resp.data["access"]}'}

    # --- Listagem -----------------------------------------------------

    def test_list_requires_auth(self):
        response = self.client.get('/portal/api/support/tickets/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_returns_only_own_clinic_tickets(self):
        auth = self._login('gerente@a.com')
        response = self.client.get('/portal/api/support/tickets/', **auth)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {t['id'] for t in response.data}
        self.assertEqual(ids, {self.ticket_a.id})

    def test_list_parses_category_from_description_header(self):
        auth = self._login('gerente@a.com')
        response = self.client.get('/portal/api/support/tickets/', **auth)

        ticket_payload = next(t for t in response.data if t['id'] == self.ticket_a.id)
        self.assertEqual(ticket_payload['category'], 'sincronizacao')

    def test_list_never_exposes_raw_description(self):
        auth = self._login('gerente@a.com')
        response = self.client.get('/portal/api/support/tickets/', **auth)
        self.assertNotIn('description', response.data[0])

    # --- Detalhe --------------------------------------------------------

    def test_detail_requires_auth(self):
        response = self.client.get(f'/portal/api/support/tickets/{self.ticket_a.id}/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_detail_happy_path_includes_messages(self):
        TicketMessage.objects.create(ticket=self.ticket_a, author=None, message='Estamos verificando.')
        auth = self._login('gerente@a.com')

        response = self.client.get(f'/portal/api/support/tickets/{self.ticket_a.id}/', **auth)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], self.ticket_a.id)
        self.assertEqual(len(response.data['messages']), 1)
        self.assertEqual(response.data['messages'][0]['message'], 'Estamos verificando.')

    def test_detail_access_generates_audit_log(self):
        """BACFF-AVULSA-11 (LGPD Art. 37) — `description` pode carregar PHI de
        terceiros citada incidentalmente pelo atendente, então todo acesso
        bem-sucedido ao detalhe do ticket precisa ficar registrado, mesmo
        padrão de `_ReportReadView` (BACFF-AVULSA-05)."""
        auth = self._login('gerente@a.com')

        response = self.client.get(f'/portal/api/support/tickets/{self.ticket_a.id}/', **auth)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        log = PortalReadAuditLog.objects.get()
        self.assertEqual(log.clinic, self.clinic_a)
        self.assertEqual(log.clinic_user, self.user_a)
        self.assertEqual(log.entity, 'support_ticket')
        self.assertEqual(log.record_count, 1)

    def test_detail_other_clinic_ticket_returns_404_not_403(self):
        """Anti-IDOR: clínica A não deve conseguir nem confirmar a existência
        de um ticket da clínica B."""
        auth_a = self._login('gerente@a.com')
        response = self.client.get(f'/portal/api/support/tickets/{self.ticket_b.id}/', **auth_a)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(PortalReadAuditLog.objects.exists())

    def test_detail_nonexistent_ticket_returns_404(self):
        auth = self._login('gerente@a.com')
        response = self.client.get('/portal/api/support/tickets/999999/', **auth)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class SupportSettingsApiViewTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.clinic_a = make_clinic('Clínica A')
        self.clinic_b = make_clinic('Clínica B')
        self.user_a = make_clinic_user(self.clinic_a, email='gerente@a.com')
        self.user_b = make_clinic_user(self.clinic_b, email='gerente@b.com')

    def _login(self, email, password='senha-123'):
        resp = self.client.post('/portal/api/auth/login/', {'email': email, 'password': password}, format='json')
        return {'HTTP_AUTHORIZATION': f'Bearer {resp.data["access"]}'}

    def test_get_requires_auth(self):
        response = self.client.get('/portal/api/support/settings/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_returns_current_flag_state(self):
        auth = self._login('gerente@a.com')
        response = self.client.get('/portal/api/support/settings/', **auth)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['support_ticket_restricted_to_admin'])

    def test_patch_persists_flag_on_own_clinic(self):
        auth = self._login('gerente@a.com')
        response = self.client.patch(
            '/portal/api/support/settings/',
            {'support_ticket_restricted_to_admin': True},
            format='json',
            **auth,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['support_ticket_restricted_to_admin'])

        self.clinic_a.refresh_from_db()
        self.assertTrue(self.clinic_a.support_ticket_restricted_to_admin)

    def test_patch_never_affects_other_clinic(self):
        """Isolamento multi-tenant: o PATCH só pode alterar `request.user.clinic`
        — nunca aceita um clinic_id explícito nem afeta outra linha."""
        auth_a = self._login('gerente@a.com')
        self.client.patch(
            '/portal/api/support/settings/',
            {'support_ticket_restricted_to_admin': True},
            format='json',
            **auth_a,
        )

        self.clinic_b.refresh_from_db()
        self.assertFalse(self.clinic_b.support_ticket_restricted_to_admin)


class TicketTemplateViewsTest(TestCase):
    """Páginas server-rendered (/portal/suporte/...), protegidas pelo
    ClinicPortalAuthMiddleware (cookie httpOnly) em vez de header Bearer."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        post_save.disconnect(sync_ticket_to_zoho, sender=Ticket)

    @classmethod
    def tearDownClass(cls):
        post_save.connect(sync_ticket_to_zoho, sender=Ticket)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.clinic_a = make_clinic('Clínica A')
        self.clinic_b = make_clinic('Clínica B')
        self.user_a = make_clinic_user(self.clinic_a, email='gerente@a.com')
        self.ticket_a = Ticket.objects.create(
            clinic=self.clinic_a, title='Chamado A', description='desc', priority='medium',
        )
        self.ticket_b = Ticket.objects.create(
            clinic=self.clinic_b, title='Chamado B', description='desc', priority='medium',
        )

    def _login_cookie(self, email='gerente@a.com', password='senha-123'):
        resp = self.client.post('/portal/api/auth/login/', {'email': email, 'password': password}, format='json')
        access = resp.data['access']
        self.client.cookies['portal_access_token'] = access

    def test_list_page_requires_auth_redirects_to_login(self):
        response = self.client.get('/portal/suporte/')
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertIn('/portal/login/', response.url)

    def test_list_page_shows_only_own_clinic_tickets(self):
        self._login_cookie()
        response = self.client.get('/portal/suporte/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        tickets_in_context = list(response.context['tickets'])
        self.assertEqual(tickets_in_context, [self.ticket_a])

    def test_detail_page_other_clinic_ticket_is_404(self):
        self._login_cookie()
        response = self.client.get(f'/portal/suporte/{self.ticket_b.id}/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_settings_page_get_requires_auth(self):
        response = self.client.get('/portal/suporte/configuracoes/')
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)

    def test_settings_page_post_persists_flag(self):
        self._login_cookie()
        response = self.client.post(
            '/portal/suporte/configuracoes/',
            {'support_ticket_restricted_to_admin': 'on'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.clinic_a.refresh_from_db()
        self.assertTrue(self.clinic_a.support_ticket_restricted_to_admin)

    def test_settings_page_post_unchecked_clears_flag(self):
        self.clinic_a.support_ticket_restricted_to_admin = True
        self.clinic_a.save(update_fields=['support_ticket_restricted_to_admin'])
        self._login_cookie()

        response = self.client.post('/portal/suporte/configuracoes/', {})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.clinic_a.refresh_from_db()
        self.assertFalse(self.clinic_a.support_ticket_restricted_to_admin)


class TicketCategoryFallbackTest(TestCase):
    """
    Investigação pontual (QA, BACFF-AVULSA-09): tickets sem o cabeçalho
    `[Categoria: ...]` — legados (pré-EDGW-052) ou criados por support user
    diretamente no Zoho/admin — não podem quebrar a listagem/detalhe do
    portal_gestor. `Ticket.category` deve degradar para None.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        post_save.disconnect(sync_ticket_to_zoho, sender=Ticket)

    @classmethod
    def tearDownClass(cls):
        post_save.connect(sync_ticket_to_zoho, sender=Ticket)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.clinic = make_clinic('Clínica Legada')
        self.user = make_clinic_user(self.clinic, email='gerente@legada.com')

    def _login(self, email='gerente@legada.com', password='senha-123'):
        resp = self.client.post('/portal/api/auth/login/', {'email': email, 'password': password}, format='json')
        return {'HTTP_AUTHORIZATION': f'Bearer {resp.data["access"]}'}

    def test_category_property_returns_none_without_header(self):
        ticket = Ticket.objects.create(
            clinic=self.clinic, title='Chamado legado', description='Login não funciona, favor verificar.',
        )
        self.assertIsNone(ticket.category)

    def test_category_property_returns_none_for_empty_description(self):
        ticket = Ticket.objects.create(clinic=self.clinic, title='Sem descrição', description='')
        self.assertIsNone(ticket.category)

    def test_list_endpoint_handles_ticket_without_category_header(self):
        Ticket.objects.create(
            clinic=self.clinic, title='Chamado legado', description='Aberto direto pelo support user, sem app.',
        )
        auth = self._login()

        response = self.client.get('/portal/api/support/tickets/', **auth)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertIsNone(response.data[0]['category'])

    def test_detail_endpoint_handles_ticket_without_category_header(self):
        ticket = Ticket.objects.create(
            clinic=self.clinic, title='Chamado legado', description='Descrição livre, sem cabeçalho estruturado.',
        )
        auth = self._login()

        response = self.client.get(f'/portal/api/support/tickets/{ticket.id}/', **auth)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data['category'])

    def test_list_page_renders_ticket_without_category_header(self):
        """Página server-rendered não deve quebrar (500) ao iterar tickets
        cujo `category` é None."""
        Ticket.objects.create(
            clinic=self.clinic, title='Chamado legado', description='Sem cabeçalho.',
        )
        resp = self.client.post(
            '/portal/api/auth/login/', {'email': 'gerente@legada.com', 'password': 'senha-123'}, format='json',
        )
        self.client.cookies['portal_access_token'] = resp.data['access']

        response = self.client.get('/portal/suporte/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)


class TicketListVolumeTest(TestCase):
    """
    Investigação pontual (QA, BACFF-AVULSA-09): a listagem hoje não pagina
    (`TicketListView` serializa `Ticket.objects.filter(clinic=...)` inteiro).
    Confirma que um volume alto não gera erro — mas isso é um ACHADO
    (documentado no relatório final), não um teste de regressão de bug: para
    uma clínica com centenas/milhares de chamados históricos, a resposta cresce
    sem limite.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        post_save.disconnect(sync_ticket_to_zoho, sender=Ticket)

    @classmethod
    def tearDownClass(cls):
        post_save.connect(sync_ticket_to_zoho, sender=Ticket)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.clinic = make_clinic('Clínica Volumosa')
        self.user = make_clinic_user(self.clinic, email='gerente@volumosa.com')
        Ticket.objects.bulk_create([
            Ticket(clinic=self.clinic, title=f'Chamado {i}', description=f'desc {i}', priority='low')
            for i in range(75)
        ])

    def test_list_endpoint_returns_all_tickets_without_error_or_pagination(self):
        auth = {'HTTP_AUTHORIZATION': f'Bearer {self._login()}'}
        response = self.client.get('/portal/api/support/tickets/', **auth)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # ACHADO: sem paginação, o payload inteiro (75 aqui, sem limite
        # superior em produção) volta em uma única resposta.
        self.assertEqual(len(response.data), 75)

    def _login(self):
        resp = self.client.post(
            '/portal/api/auth/login/', {'email': 'gerente@volumosa.com', 'password': 'senha-123'}, format='json',
        )
        return resp.data['access']


class SupportSettingsConcurrencyTest(TestCase):
    """
    Investigação pontual (QA, BACFF-AVULSA-09): dois admins da mesma clínica
    (ou duas abas do mesmo admin) alterando a flag quase simultaneamente.
    `SupportSettingsView.patch`/`TicketSettingsView (template).post` fazem um
    save() direto sem select_for_update/optimistic locking — last-write-wins
    é aceitável para este campo (não há merge de dados, é um único boolean),
    mas o teste confirma que não há corrupção (o valor final é sempre um dos
    dois escritos, nunca um estado inconsistente/parcial).
    """

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.clinic = make_clinic('Clínica Concorrente')
        self.user_a = make_clinic_user(self.clinic, email='admin1@c.com')
        self.user_b = make_clinic_user(self.clinic, email='admin2@c.com')

    def _login(self, email, password='senha-123'):
        resp = self.client.post('/portal/api/auth/login/', {'email': email, 'password': password}, format='json')
        return {'HTTP_AUTHORIZATION': f'Bearer {resp.data["access"]}'}

    def test_last_write_wins_without_corrupting_field(self):
        # Simula duas requests concorrentes: ambas carregam a clínica com o
        # valor original (False) antes de qualquer uma delas escrever.
        auth_a = self._login('admin1@c.com')
        auth_b = self._login('admin2@c.com')

        clinic_snapshot_for_a = Clinic.objects.get(pk=self.clinic.pk)
        clinic_snapshot_for_b = Clinic.objects.get(pk=self.clinic.pk)
        self.assertFalse(clinic_snapshot_for_a.support_ticket_restricted_to_admin)
        self.assertFalse(clinic_snapshot_for_b.support_ticket_restricted_to_admin)

        # Admin A liga a flag primeiro.
        response_a = self.client.patch(
            '/portal/api/support/settings/', {'support_ticket_restricted_to_admin': True},
            format='json', **auth_a,
        )
        self.assertEqual(response_a.status_code, status.HTTP_200_OK)

        # Admin B, com base num snapshot mais antigo (antes do save de A),
        # também escreve — last-write-wins: o valor final é o de B.
        response_b = self.client.patch(
            '/portal/api/support/settings/', {'support_ticket_restricted_to_admin': False},
            format='json', **auth_b,
        )
        self.assertEqual(response_b.status_code, status.HTTP_200_OK)

        self.clinic.refresh_from_db()
        # Corrupção seria: valor nem True nem False (ex.: None, string, ou
        # exceção de integridade). O campo continua um boolean válido — o
        # último PATCH (B) prevalece, que é o comportamento aceitável aqui.
        self.assertIs(self.clinic.support_ticket_restricted_to_admin, False)
        self.assertIsInstance(self.clinic.support_ticket_restricted_to_admin, bool)
