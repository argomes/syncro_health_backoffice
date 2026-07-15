"""
Prova de isolamento de tenant no Django Admin do app accounts (ClinicAccess,
ClinicUser) — mesmo gap já corrigido em tiss/admin.py e clinics/admin.py.

MEDIUM: ninguém tem essas permissões concedidas por padrão hoje
(seed_admin_groups.py), então os testes concedem manualmente para provar que
o mixin isola corretamente de qualquer forma.
"""
import uuid

from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from clinics.models import Clinic, ClinicStatus, Plan

from .models import ClinicAccess, ClinicUser, SupportUser


def make_clinic(**overrides):
    defaults = dict(
        name='Clínica Teste Accounts',
        slug=f'clinica-{uuid.uuid4().hex[:8]}',
        plan=Plan.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
        db_name=f'clinic_{uuid.uuid4().hex[:8]}',
        db_user=f'u_{uuid.uuid4().hex[:8]}',
    )
    defaults.update(overrides)
    return Clinic.objects.create(**defaults)


class ClinicAccessAdminTenantScopingTest(TestCase):
    def setUp(self):
        self.clinic_a = make_clinic(name='Clínica CA A')
        self.clinic_b = make_clinic(name='Clínica CA B')

        self.other_user_a = SupportUser.objects.create_user(
            username='other_a', email='other_a@syncro.test', password='x',
        )
        self.other_user_b = SupportUser.objects.create_user(
            username='other_b', email='other_b@syncro.test', password='x',
        )
        self.access_a = ClinicAccess.objects.create(
            support_user=self.other_user_a, clinic=self.clinic_a, role='viewer',
        )
        self.access_b = ClinicAccess.objects.create(
            support_user=self.other_user_b, clinic=self.clinic_b, role='viewer',
        )

        self.analyst = SupportUser.objects.create_user(
            username='analista_ca', email='analista_ca@syncro.test',
            password='senha-teste-123', is_staff=True,
        )
        self.analyst.user_permissions.add(Permission.objects.get(codename='view_clinicaccess'))
        ClinicAccess.objects.create(support_user=self.analyst, clinic=self.clinic_a, role='viewer')

        self.client = Client()
        self.client.force_login(self.analyst)

    def test_analyst_does_not_see_other_clinic_access(self):
        url = reverse('admin:accounts_clinicaccess_changelist')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'other_a')
        self.assertNotContains(response, 'other_b')

    def test_superuser_sees_all_clinic_accesses(self):
        superuser = SupportUser.objects.create_superuser(
            username='root_ca', email='root_ca@syncro.test', password='senha-teste-123',
        )
        client = Client()
        client.force_login(superuser)
        response = client.get(reverse('admin:accounts_clinicaccess_changelist'))
        self.assertContains(response, 'other_a')
        self.assertContains(response, 'other_b')


class ClinicUserAdminTenantScopingTest(TestCase):
    def setUp(self):
        self.clinic_a = make_clinic(name='Clínica CU A')
        self.clinic_b = make_clinic(name='Clínica CU B')

        self.clinic_user_a = ClinicUser.objects.create(
            clinic=self.clinic_a, email='cu_a@clinica.test', name='Usuário CU A',
        )
        self.clinic_user_b = ClinicUser.objects.create(
            clinic=self.clinic_b, email='cu_b@clinica.test', name='Usuário CU B',
        )

        self.analyst = SupportUser.objects.create_user(
            username='analista_cu', email='analista_cu@syncro.test',
            password='senha-teste-123', is_staff=True,
        )
        self.analyst.user_permissions.add(Permission.objects.get(codename='view_clinicuser'))
        ClinicAccess.objects.create(support_user=self.analyst, clinic=self.clinic_a, role='viewer')

        self.client = Client()
        self.client.force_login(self.analyst)

    def test_analyst_does_not_see_other_clinic_user(self):
        url = reverse('admin:accounts_clinicuser_changelist')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'cu_a@clinica.test')
        self.assertNotContains(response, 'cu_b@clinica.test')

    def test_superuser_sees_all_clinic_users(self):
        superuser = SupportUser.objects.create_superuser(
            username='root_cu', email='root_cu@syncro.test', password='senha-teste-123',
        )
        client = Client()
        client.force_login(superuser)
        response = client.get(reverse('admin:accounts_clinicuser_changelist'))
        self.assertContains(response, 'cu_a@clinica.test')
        self.assertContains(response, 'cu_b@clinica.test')
