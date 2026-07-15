import uuid

from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase

from accounts.models import ClinicAccess, SupportUser
from .admin import ClinicAdmin
from .models import Clinic


def _make_clinic(slug):
    return Clinic.objects.create(
        name=f'Clínica {slug}', slug=slug, cnpj=f'{uuid.uuid4().hex[:14]}',
        db_name=f'db_{uuid.uuid4().hex[:8]}', db_user=f'u_{uuid.uuid4().hex[:8]}',
    )


class TenantScopedClinicAdminTests(TestCase):
    """
    TASK-BO-11 fix: prova que ClinicAdmin não vaza clínicas fora do escopo
    de ClinicAccess para um SupportUser não-admin, e que a action
    `create_asaas_subscription` (que dispara cobrança recorrente real via
    Asaas) não pode ser disparada contra uma clínica fora desse escopo.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()

        self.clinic_a = _make_clinic('clinic-a')
        self.clinic_b = _make_clinic('clinic-b')

        self.admin_user = SupportUser.objects.create_superuser(
            username='superadmin', email='superadmin@test.com', password='x',
        )
        self.role_admin = SupportUser.objects.create_user(
            username='role_admin', email='role_admin@test.com', password='x',
            role=SupportUser.Role.ADMIN,
        )
        self.analyst_a = SupportUser.objects.create_user(
            username='analyst_a', email='analyst_a@test.com', password='x',
            role=SupportUser.Role.SUPPORT,
        )
        ClinicAccess.objects.create(support_user=self.analyst_a, clinic=self.clinic_a, role='viewer')

    def _request_for(self, user):
        request = self.factory.get('/admin/clinics/clinic/')
        request.user = user
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def test_superuser_sees_all_clinics(self):
        admin = ClinicAdmin(Clinic, self.site)
        qs = admin.get_queryset(self._request_for(self.admin_user))
        self.assertEqual(set(qs.values_list('id', flat=True)), {self.clinic_a.id, self.clinic_b.id})

    def test_role_admin_sees_all_clinics(self):
        admin = ClinicAdmin(Clinic, self.site)
        qs = admin.get_queryset(self._request_for(self.role_admin))
        self.assertEqual(set(qs.values_list('id', flat=True)), {self.clinic_a.id, self.clinic_b.id})

    def test_non_admin_analyst_only_sees_own_clinic(self):
        admin = ClinicAdmin(Clinic, self.site)
        qs = admin.get_queryset(self._request_for(self.analyst_a))
        self.assertEqual(list(qs.values_list('id', flat=True)), [self.clinic_a.id])

    def test_analyst_without_any_access_sees_nothing(self):
        analyst_none = SupportUser.objects.create_user(
            username='analyst_none', email='analyst_none@test.com', password='x',
            role=SupportUser.Role.SUPPORT,
        )
        admin = ClinicAdmin(Clinic, self.site)
        qs = admin.get_queryset(self._request_for(analyst_none))
        self.assertEqual(list(qs), [])

    def test_non_admin_analyst_cannot_create_asaas_subscription_for_other_clinic(self):
        """
        Simula o fluxo real do admin: a action só opera sobre o queryset já
        filtrado por get_queryset — mesmo chamando a action diretamente com
        um queryset "cru" contendo a Clínica B, o queryset efetivo que o
        admin usaria (via changelist) nunca a incluiria para este usuário.
        """
        admin = ClinicAdmin(Clinic, self.site)
        request = self._request_for(self.analyst_a)
        scoped_qs = admin.get_queryset(request)

        # A Clínica B não está no queryset que o /admin/ apresentaria/agiria
        # para este usuário — a action nunca a alcança.
        self.assertNotIn(self.clinic_b.id, scoped_qs.values_list('id', flat=True))

        admin.create_asaas_subscription(request, scoped_qs)

        self.clinic_a.refresh_from_db()
        self.clinic_b.refresh_from_db()
        self.assertTrue(self.clinic_a.asaas_subscription_id)
        self.assertFalse(self.clinic_b.asaas_subscription_id)

    def test_role_admin_can_create_asaas_subscription_for_any_clinic(self):
        admin = ClinicAdmin(Clinic, self.site)
        request = self._request_for(self.role_admin)
        scoped_qs = admin.get_queryset(request)

        admin.create_asaas_subscription(request, scoped_qs)

        self.clinic_a.refresh_from_db()
        self.clinic_b.refresh_from_db()
        self.assertTrue(self.clinic_a.asaas_subscription_id)
        self.assertTrue(self.clinic_b.asaas_subscription_id)
