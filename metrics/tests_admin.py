"""
Prova de isolamento de tenant no Django Admin do app metrics (SystemHeartbeat,
SystemLog) — mesmo gap já corrigido em tiss/admin.py e clinics/admin.py.

Usa o grupo real "Analista Operacional" (seed_admin_groups), que hoje já tem
view_systemheartbeat/view_systemlog concedidos em produção — sem o
TenantScopedAdminMixin, esse grupo vazaria dados de todas as clínicas.
"""
import uuid
from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import ClinicAccess, SupportUser
from clinics.models import Clinic, ClinicStatus, Plan

from .models import SystemHeartbeat, SystemLog


def make_clinic(**overrides):
    defaults = dict(
        name='Clínica Teste Metrics',
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


class MetricsAdminTenantScopingTest(TestCase):
    def setUp(self):
        seed_groups()
        self.clinic_a = make_clinic(name='Clínica A')
        self.clinic_b = make_clinic(name='Clínica B')

        self.heartbeat_a = SystemHeartbeat.objects.create(
            clinic=self.clinic_a, gateway_version='1.0.0',
        )
        self.heartbeat_b = SystemHeartbeat.objects.create(
            clinic=self.clinic_b, gateway_version='1.0.0',
        )
        self.log_a = SystemLog.objects.create(
            clinic=self.clinic_a, level='error', message='erro clínica A',
            occurred_at=timezone.now(),
        )
        self.log_b = SystemLog.objects.create(
            clinic=self.clinic_b, level='error', message='erro clínica B',
            occurred_at=timezone.now(),
        )

        self.analyst = SupportUser.objects.create_user(
            username='analista_metrics', email='analista_metrics@syncro.test',
            password='senha-teste-123', is_staff=True,
        )
        self.analyst.groups.add(Group.objects.get(name='Analista Operacional'))
        ClinicAccess.objects.create(support_user=self.analyst, clinic=self.clinic_a, role='viewer')

        self.client = Client()
        self.client.force_login(self.analyst)

    def test_analyst_does_not_see_other_clinic_heartbeat_in_changelist(self):
        from django.urls import reverse
        url = reverse('admin:metrics_systemheartbeat_changelist')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.clinic_a.name)
        self.assertNotContains(response, self.clinic_b.name)

    def test_analyst_does_not_see_other_clinic_log_in_changelist(self):
        from django.urls import reverse
        url = reverse('admin:metrics_systemlog_changelist')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'erro clínica A')
        self.assertNotContains(response, 'erro clínica B')

    def test_superuser_sees_all_heartbeats_and_logs(self):
        superuser = SupportUser.objects.create_superuser(
            username='root_metrics', email='root_metrics@syncro.test', password='senha-teste-123',
        )
        client = Client()
        client.force_login(superuser)
        from django.urls import reverse

        response = client.get(reverse('admin:metrics_systemheartbeat_changelist'))
        self.assertContains(response, self.clinic_a.name)
        self.assertContains(response, self.clinic_b.name)

        response = client.get(reverse('admin:metrics_systemlog_changelist'))
        self.assertContains(response, 'erro clínica A')
        self.assertContains(response, 'erro clínica B')
