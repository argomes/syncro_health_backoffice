"""
Prova de isolamento de tenant no Django Admin do app portal_gestor
(ReportSession, ClinicUserNoticeDismissal) — mesmo gap já corrigido em
tiss/admin.py e clinics/admin.py.

ReportSession: usa o grupo real "Analista Operacional" (seed_admin_groups),
que hoje já tem view_reportsession concedido em produção.

ClinicUserNoticeDismissal: ninguém tem essa permissão concedida por padrão
hoje, então o teste concede manualmente para provar que o mixin se comporta
corretamente de qualquer forma.
"""
import uuid
from io import StringIO

from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import ClinicAccess, ClinicUser, SupportUser
from clinics.models import Clinic, ClinicStatus, Plan

from .models import ClinicUserNoticeDismissal, ProductNotice, ReportSession


def make_clinic(**overrides):
    defaults = dict(
        name='Clínica Teste Portal Gestor',
        slug=f'clinica-{uuid.uuid4().hex[:8]}',
        plan=Plan.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
        db_name=f'clinic_{uuid.uuid4().hex[:8]}',
        db_user=f'u_{uuid.uuid4().hex[:8]}',
    )
    defaults.update(overrides)
    return Clinic.objects.create(**defaults)


def seed_groups():
    out = StringIO()
    call_command('seed_admin_groups', stdout=out)
    return out.getvalue()


class ReportSessionAdminTenantScopingTest(TestCase):
    """HIGH — permissão view_reportsession já concedida ao grupo real."""

    def setUp(self):
        seed_groups()
        self.clinic_a = make_clinic(name='Clínica RS A')
        self.clinic_b = make_clinic(name='Clínica RS B')

        now = timezone.now()
        self.session_a = ReportSession.objects.create(
            clinic=self.clinic_a, date_from=now, date_to=now, expires_at=now,
        )
        self.session_b = ReportSession.objects.create(
            clinic=self.clinic_b, date_from=now, date_to=now, expires_at=now,
        )

        self.analyst = SupportUser.objects.create_user(
            username='analista_rs', email='analista_rs@syncro.test',
            password='senha-teste-123', is_staff=True,
        )
        self.analyst.groups.add(Group.objects.get(name='Analista Operacional'))
        ClinicAccess.objects.create(support_user=self.analyst, clinic=self.clinic_a, role='viewer')

        self.client = Client()
        self.client.force_login(self.analyst)

    def test_analyst_does_not_see_other_clinic_report_session(self):
        url = reverse('admin:portal_gestor_reportsession_changelist')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(self.session_a.session_id))
        self.assertNotContains(response, str(self.session_b.session_id))

    def test_superuser_sees_all_report_sessions(self):
        superuser = SupportUser.objects.create_superuser(
            username='root_rs', email='root_rs@syncro.test', password='senha-teste-123',
        )
        client = Client()
        client.force_login(superuser)
        response = client.get(reverse('admin:portal_gestor_reportsession_changelist'))
        self.assertContains(response, str(self.session_a.session_id))
        self.assertContains(response, str(self.session_b.session_id))


class ClinicUserNoticeDismissalAdminTenantScopingTest(TestCase):
    """
    MEDIUM — ninguém tem view_clinicusernoticedismissal por padrão hoje;
    concedemos manualmente para provar que o mixin isola corretamente mesmo
    assim (comportamento correto independente de quem tem a permissão hoje).
    """

    def setUp(self):
        self.clinic_a = make_clinic(name='Clínica Notice A')
        self.clinic_b = make_clinic(name='Clínica Notice B')

        self.clinic_user_a = ClinicUser.objects.create(
            clinic=self.clinic_a, email='user_a@clinica.test', name='Usuário A',
        )
        self.clinic_user_b = ClinicUser.objects.create(
            clinic=self.clinic_b, email='user_b@clinica.test', name='Usuário B',
        )
        self.notice = ProductNotice.objects.create(
            title='Aviso Teste', body='corpo', starts_at=timezone.now(), ends_at=timezone.now(),
        )
        self.dismissal_a = ClinicUserNoticeDismissal.objects.create(
            clinic_user=self.clinic_user_a, notice=self.notice,
        )
        self.dismissal_b = ClinicUserNoticeDismissal.objects.create(
            clinic_user=self.clinic_user_b, notice=self.notice,
        )

        self.analyst = SupportUser.objects.create_user(
            username='analista_notice', email='analista_notice@syncro.test',
            password='senha-teste-123', is_staff=True,
        )
        self.analyst.user_permissions.add(
            Permission.objects.get(codename='view_clinicusernoticedismissal'),
        )
        ClinicAccess.objects.create(support_user=self.analyst, clinic=self.clinic_a, role='viewer')

        self.client = Client()
        self.client.force_login(self.analyst)

    def test_analyst_does_not_see_other_clinic_dismissal(self):
        url = reverse('admin:portal_gestor_clinicusernoticedismissal_changelist')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.clinic_user_a.email)
        self.assertNotContains(response, self.clinic_user_b.email)

    def test_superuser_sees_all_dismissals(self):
        superuser = SupportUser.objects.create_superuser(
            username='root_notice', email='root_notice@syncro.test', password='senha-teste-123',
        )
        client = Client()
        client.force_login(superuser)
        response = client.get(reverse('admin:portal_gestor_clinicusernoticedismissal_changelist'))
        self.assertContains(response, self.clinic_user_a.email)
        self.assertContains(response, self.clinic_user_b.email)
