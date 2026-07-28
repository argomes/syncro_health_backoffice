"""
TASK-056 — testes do seed de grupos/permissões do admin e da proteção de
campos sensíveis em ClinicAdmin.
"""
import uuid
from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from clinics.models import Clinic, ClinicStatus, Plan

from .models import ClinicAccess, SupportUser


def make_clinic(**overrides):
    defaults = dict(
        name='Clínica Teste TASK-056',
        slug=f'clinica-{uuid.uuid4().hex[:8]}',
        plan=Plan.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
        db_name=f'clinic_{uuid.uuid4().hex[:8]}',
        db_user=f'u_{uuid.uuid4().hex[:8]}',
        db_password_encrypted='ciphertext-fake-nao-real',
        public_key_pem='-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----',
    )
    defaults.update(overrides)
    return Clinic.objects.create(**defaults)


def seed_groups():
    out = StringIO()
    call_command('seed_admin_groups', stdout=out)
    return out.getvalue()


class SeedAdminGroupsCommandTest(TestCase):
    def test_creates_analista_operacional_with_expected_permissions(self):
        seed_groups()
        group = Group.objects.get(name='Analista Operacional')
        codenames = set(group.permissions.values_list('codename', flat=True))

        self.assertIn('view_clinic', codenames)
        self.assertIn('view_systemheartbeat', codenames)
        self.assertIn('view_systemlog', codenames)
        self.assertIn('view_reportsession', codenames)
        self.assertIn('add_ticket', codenames)
        self.assertIn('change_ticket', codenames)

        # Não deve ter delete em nada, nem qualquer permissão sobre modelos
        # de credencial (ClinicUser/SupportUser/ClinicAccess).
        self.assertFalse(any(c.startswith('delete_') for c in codenames))
        self.assertFalse(any('clinicuser' in c for c in codenames))
        self.assertFalse(any('supportuser' in c for c in codenames))
        self.assertFalse(any('clinicaccess' in c for c in codenames))

    def test_creates_financeiro_group(self):
        seed_groups()
        group = Group.objects.get(name='Financeiro')
        codenames = set(group.permissions.values_list('codename', flat=True))
        self.assertIn('view_clinic', codenames)

    def test_idempotent_does_not_duplicate_groups(self):
        seed_groups()
        seed_groups()
        self.assertEqual(Group.objects.filter(name='Analista Operacional').count(), 1)
        self.assertEqual(Group.objects.filter(name='Financeiro').count(), 1)

    def test_rerunning_removes_stale_permissions(self):
        seed_groups()
        group = Group.objects.get(name='Analista Operacional')
        # Simula permissão indevida atribuída manualmente por engano.
        from django.contrib.auth.models import Permission
        stray = Permission.objects.filter(codename='delete_clinic').first()
        group.permissions.add(stray)
        self.assertIn('delete_clinic', group.permissions.values_list('codename', flat=True))

        seed_groups()
        group.refresh_from_db()
        self.assertNotIn('delete_clinic', group.permissions.values_list('codename', flat=True))


class ClinicAdminSensitiveFieldsTest(TestCase):
    """
    Um usuário do grupo Analista Operacional tem view_clinic, mas nunca deve
    ver license_key/public_key_pem/db_password_encrypted — mesmo que essas
    colunas apareçam no list_display/fields por padrão do ModelAdmin.
    """

    def setUp(self):
        seed_groups()
        self.clinic = make_clinic()

        self.analyst = SupportUser.objects.create_user(
            username='analista', email='analista@syncro.test', password='senha-teste-123',
            is_staff=True,
        )
        self.analyst.groups.add(Group.objects.get(name='Analista Operacional'))
        # TASK-BO-11 fix: ClinicAdmin agora usa TenantScopedAdminMixin, então
        # o analista só enxerga clínicas com ClinicAccess ativo — sem isso,
        # os testes abaixo (que dependem de acessar self.clinic) 404/302.
        ClinicAccess.objects.create(support_user=self.analyst, clinic=self.clinic, role='viewer')

        self.client = Client()
        self.client.force_login(self.analyst)

    def test_analyst_changelist_hides_sensitive_columns(self):
        url = reverse('admin:clinics_clinic_changelist')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.clinic.db_password_encrypted)

    def test_analyst_change_view_excludes_sensitive_fields(self):
        url = reverse('admin:clinics_clinic_change', args=[self.clinic.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.clinic.public_key_pem)
        self.assertNotContains(response, self.clinic.db_password_encrypted)
        self.assertNotContains(response, str(self.clinic.license_key))

    def test_superuser_still_sees_sensitive_fields(self):
        superuser = SupportUser.objects.create_superuser(
            username='root', email='root@syncro.test', password='senha-teste-123',
        )
        self.client.force_login(superuser)
        url = reverse('admin:clinics_clinic_change', args=[self.clinic.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(self.clinic.license_key))

    def test_analyst_cannot_access_clinic_user_admin(self):
        url = reverse('admin:accounts_clinicuser_changelist')
        response = self.client.get(url)
        # Sem nenhuma permissão sobre ClinicUser, o Django admin nega acesso
        # (redireciona pro login/mostra 403), nunca lista o changelist.
        self.assertNotEqual(response.status_code, 200)


class AdminSidebarNavigationTest(TestCase):
    """
    TASK-057 — a sidebar do Unfold (UNFOLD["SIDEBAR"]["navigation"]) mostra
    só os itens que o usuário tem permissão de ver, controlado pelos grupos
    da TASK-056.
    """

    def setUp(self):
        seed_groups()
        self.client = Client()

    def test_analyst_sees_operational_items_not_admin_only(self):
        analyst = SupportUser.objects.create_user(
            username='analista2', email='analista2@syncro.test', password='senha-teste-123',
            is_staff=True,
        )
        analyst.groups.add(Group.objects.get(name='Analista Operacional'))
        self.client.force_login(analyst)

        response = self.client.get(reverse('admin:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Clínicas')
        self.assertContains(response, 'Gateways')
        self.assertContains(response, 'Relatórios')
        # Sem is_superuser, não deve ver o grupo restrito de Usuários & Permissões.
        self.assertNotContains(response, 'Usuários & Permissões')

    def test_superuser_sees_all_items(self):
        superuser = SupportUser.objects.create_superuser(
            username='root2', email='root2@syncro.test', password='senha-teste-123',
        )
        self.client.force_login(superuser)

        response = self.client.get(reverse('admin:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Clínicas')
        self.assertContains(response, 'Gateways')
        self.assertContains(response, 'Usuários & Permissões')

    def test_staff_with_no_groups_sees_only_dashboard(self):
        bare_staff = SupportUser.objects.create_user(
            username='semgrupo', email='semgrupo@syncro.test', password='senha-teste-123',
            is_staff=True,
        )
        self.client.force_login(bare_staff)

        response = self.client.get(reverse('admin:index'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Gateways')
        self.assertNotContains(response, 'Usuários & Permissões')


class AdminSidebarReorgTest(TestCase):
    """
    ADMIN-DASHBOARD-REDESIGN — reorganização em 5 grupos (Principal /
    Administrar / Operações / Configurações / Sistema). Prova que os itens
    novos (antes órfãos, só alcançáveis por URL direta) agora aparecem no
    menu pro superuser, e que todo reverse_lazy do sidebar resolve — o bug
    do UNFOLD["TABS"] apontando pra "clinicas.clinica" (app/model
    inexistente) existiu por meses justamente por faltar um teste destes.
    """

    def setUp(self):
        self.client = Client()
        self.superuser = SupportUser.objects.create_superuser(
            username='root-sidebar', email='root-sidebar@syncro.test', password='senha-teste-123',
        )
        self.client.force_login(self.superuser)

    def test_all_sidebar_links_resolve(self):
        from django.conf import settings

        for group in settings.UNFOLD['SIDEBAR']['navigation']:
            for item in group['items']:
                # Links são reverse_lazy — força a resolução; se algum
                # reverse_lazy tivesse app_label/model errado (como o TABS
                # morto tinha), isso já teria levantado NoReverseMatch na
                # hora de montar o settings.py, mas o teste também garante
                # que o link vira uma URL de verdade, não um placeholder.
                self.assertTrue(str(item['link']).startswith('/'))

    def test_superuser_sees_previously_orphaned_items(self):
        response = self.client.get(reverse('admin:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Usuários do cliente')
        self.assertContains(response, 'Faturas')
        self.assertContains(response, 'Planos')
        self.assertContains(response, 'Feriados')
        self.assertContains(response, 'Operadoras ANS')
        self.assertContains(response, 'Credenciais de operadora')
        self.assertContains(response, 'Tabela TUSS')
        self.assertContains(response, 'Municípios (IBGE)')
        self.assertContains(response, 'Auditoria de leitura (LGPD)')
        self.assertContains(response, 'Acessos de suporte')
        self.assertContains(response, 'Avisos de produto')

    def test_dead_tabs_config_removed(self):
        from django.conf import settings

        self.assertNotIn('TABS', settings.UNFOLD)
