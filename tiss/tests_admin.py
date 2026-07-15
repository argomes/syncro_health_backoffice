import uuid
from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase

from accounts.models import ClinicAccess, SupportUser
from clinics.models import Clinic
from .admin import TISSOperatorConfigAdmin, TISSLoteAdmin, TISSGuiaAdmin, TISSGlosaAdmin
from .models import TISSOperatorConfig, TISSLote, TISSGuia, TISSGlosa


def _make_clinic(slug):
    return Clinic.objects.create(
        name=f'Clínica {slug}', slug=slug, cnpj=f'{uuid.uuid4().hex[:14]}',
        db_name=f'db_{uuid.uuid4().hex[:8]}', db_user=f'u_{uuid.uuid4().hex[:8]}',
    )


class TenantScopedAdminTests(TestCase):
    """
    Prova que o Django Admin do app tiss (BO-SEC-011) não vaza PHI
    (beneficiario_nome/numero_carteira) entre clínicas para um staff com
    view_* mas sem ClinicAccess — mesmo isolamento já aplicado na API REST.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()

        self.clinic_a = _make_clinic('admin-clinic-a')
        self.clinic_b = _make_clinic('admin-clinic-b')

        self.op_a = TISSOperatorConfig.objects.create(
            clinic=self.clinic_a, nome_operadora='Orizon', registro_ans='111111', endpoint_url='https://a',
        )
        self.op_b = TISSOperatorConfig.objects.create(
            clinic=self.clinic_b, nome_operadora='Orizon', registro_ans='222222', endpoint_url='https://b',
        )
        self.lote_a = TISSLote.objects.create(clinic=self.clinic_a, operator_config=self.op_a, numero_lote=1, competencia='2026-07')
        self.lote_b = TISSLote.objects.create(clinic=self.clinic_b, operator_config=self.op_b, numero_lote=1, competencia='2026-07')

        self.guia_a = TISSGuia.objects.create(
            clinic=self.clinic_a, lote=self.lote_a, numero='1', competencia='2026-07',
            valor=Decimal('100.00'), beneficiario_nome='Paciente Sigiloso A', numero_carteira='CARTA111',
        )
        self.guia_b = TISSGuia.objects.create(
            clinic=self.clinic_b, lote=self.lote_b, numero='1', competencia='2026-07',
            valor=Decimal('200.00'), beneficiario_nome='Paciente Sigiloso B', numero_carteira='CARTB222',
        )
        self.glosa_b = TISSGlosa.objects.create(guia=self.guia_b, codigo='0052', valor_glosado=Decimal('50.00'))

        self.admin_user = SupportUser.objects.create_superuser(
            username='superadmin', email='superadmin@test.com', password='x',
        )
        self.analyst_a = SupportUser.objects.create_user(
            username='analyst_a', email='analyst_a@test.com', password='x', role=SupportUser.Role.SUPPORT,
        )
        ClinicAccess.objects.create(support_user=self.analyst_a, clinic=self.clinic_a, role='viewer')

    def _request_for(self, user):
        request = self.factory.get('/admin/tiss/tissguia/')
        request.user = user
        return request

    def test_superuser_sees_all_guias(self):
        admin = TISSGuiaAdmin(TISSGuia, self.site)
        qs = admin.get_queryset(self._request_for(self.admin_user))
        self.assertEqual(set(qs.values_list('id', flat=True)), {self.guia_a.id, self.guia_b.id})

    def test_non_admin_analyst_only_sees_own_clinic_guias(self):
        """PHI da Clínica B (nome/carteirinha) não deve aparecer para analista sem acesso a ela."""
        admin = TISSGuiaAdmin(TISSGuia, self.site)
        qs = admin.get_queryset(self._request_for(self.analyst_a))
        self.assertEqual(list(qs.values_list('id', flat=True)), [self.guia_a.id])
        self.assertNotIn(self.guia_b.beneficiario_nome, [g.beneficiario_nome for g in qs])

    def test_non_admin_analyst_only_sees_own_clinic_lotes(self):
        admin = TISSLoteAdmin(TISSLote, self.site)
        qs = admin.get_queryset(self._request_for(self.analyst_a))
        self.assertEqual(list(qs.values_list('id', flat=True)), [self.lote_a.id])

    def test_non_admin_analyst_only_sees_own_clinic_operator_configs(self):
        admin = TISSOperatorConfigAdmin(TISSOperatorConfig, self.site)
        qs = admin.get_queryset(self._request_for(self.analyst_a))
        self.assertEqual(list(qs.values_list('id', flat=True)), [self.op_a.id])

    def test_non_admin_analyst_does_not_see_glosa_of_other_clinic(self):
        """TISSGlosa não tem FK direta a Clinic — isolamento via guia__clinic."""
        admin = TISSGlosaAdmin(TISSGlosa, self.site)
        qs = admin.get_queryset(self._request_for(self.analyst_a))
        self.assertEqual(list(qs.values_list('id', flat=True)), [])

    def test_analyst_without_any_access_sees_nothing(self):
        analyst_none = SupportUser.objects.create_user(
            username='analyst_none', email='analyst_none@test.com', password='x', role=SupportUser.Role.SUPPORT,
        )
        admin = TISSGuiaAdmin(TISSGuia, self.site)
        qs = admin.get_queryset(self._request_for(analyst_none))
        self.assertEqual(list(qs), [])
