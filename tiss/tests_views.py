import uuid
from decimal import Decimal

from rest_framework.test import APITestCase
from rest_framework import status

from accounts.models import SupportUser, ClinicAccess
from clinics.models import Clinic
from .models import TISSOperatorConfig, TISSLote, TISSGuia, TISSGlosa


def _make_clinic(slug):
    return Clinic.objects.create(
        name=f'Clínica {slug}', slug=slug, cnpj=f'{uuid.uuid4().hex[:14]}',
        db_name=f'db_{uuid.uuid4().hex[:8]}', db_user=f'u_{uuid.uuid4().hex[:8]}',
    )


class TISSEstatisticasEndpointTests(APITestCase):
    def setUp(self):
        self.clinic_a = _make_clinic('stats-clinic-a')
        self.clinic_b = _make_clinic('stats-clinic-b')

        self.op_a = TISSOperatorConfig.objects.create(
            clinic=self.clinic_a, nome_operadora='Orizon', registro_ans='123456', endpoint_url='https://a',
        )
        self.op_b = TISSOperatorConfig.objects.create(
            clinic=self.clinic_b, nome_operadora='Orizon', registro_ans='654321', endpoint_url='https://b',
        )
        TISSLote.objects.create(clinic=self.clinic_a, operator_config=self.op_a, numero_lote=1, competencia='2026-07')
        TISSLote.objects.create(clinic=self.clinic_b, operator_config=self.op_b, numero_lote=1, competencia='2026-07')

        guia_a1 = TISSGuia.objects.create(clinic=self.clinic_a, numero='1', competencia='2026-07', valor=Decimal('100.00'), status='aceita')
        guia_a2 = TISSGuia.objects.create(clinic=self.clinic_a, numero='2', competencia='2026-07', valor=Decimal('50.00'), status='glosada')
        TISSGlosa.objects.create(guia=guia_a2, codigo='0052', descricao='Glosa teste', valor_glosado=Decimal('50.00'))

        TISSGuia.objects.create(clinic=self.clinic_b, numero='1', competencia='2026-07', valor=Decimal('999.00'), status='aceita')

        self.admin_user = SupportUser.objects.create_user(username='admin1', password='x', role=SupportUser.Role.ADMIN)
        self.billing_user_a = SupportUser.objects.create_user(username='billing1', password='x', role=SupportUser.Role.BILLING)
        ClinicAccess.objects.create(support_user=self.billing_user_a, clinic=self.clinic_a, role='viewer')

    def test_admin_ve_estatisticas_de_todas_as_clinicas(self):
        self.client.force_authenticate(user=self.admin_user)
        resp = self.client.get('/api/tiss/estatisticas/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['total_lotes'], 2)
        self.assertEqual(resp.data['valor_apresentado'], 1149.0)

    def test_billing_user_so_ve_estatisticas_da_clinica_com_acesso(self):
        self.client.force_authenticate(user=self.billing_user_a)
        resp = self.client.get('/api/tiss/estatisticas/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['total_lotes'], 1)
        self.assertEqual(resp.data['valor_apresentado'], 150.0)
        self.assertEqual(resp.data['valor_glosado'], 50.0)
        self.assertEqual(len(resp.data['top_glosas']), 1)
        self.assertEqual(resp.data['top_glosas'][0]['codigo'], '0052')

    def test_endpoint_exige_autenticacao(self):
        resp = self.client.get('/api/tiss/estatisticas/')
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_filtro_por_clinica_nao_vaza_para_usuario_sem_acesso(self):
        # billing_user_a não tem ClinicAccess para clinic_b — mesmo pedindo
        # explicitamente ?clinic=<clinic_b.id>, a query já vem filtrada pelas
        # clínicas permitidas (allowed_clinic_ids) antes do filtro por query
        # param, então o resultado é vazio, não os dados da clínica B.
        self.client.force_authenticate(user=self.billing_user_a)
        resp = self.client.get(f'/api/tiss/estatisticas/?clinic={self.clinic_b.id}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['total_lotes'], 0)
        self.assertEqual(resp.data['valor_apresentado'], 0.0)


class TISSGuiaViewSetIsolationTests(APITestCase):
    def setUp(self):
        self.clinic_a = _make_clinic('vs-isolamento-a')
        self.clinic_b = _make_clinic('vs-isolamento-b')
        self.guia_a = TISSGuia.objects.create(clinic=self.clinic_a, numero='A1', competencia='2026-07', valor=Decimal('10.00'))
        self.guia_b = TISSGuia.objects.create(clinic=self.clinic_b, numero='B1', competencia='2026-07', valor=Decimal('20.00'))
        self.billing_user_a = SupportUser.objects.create_user(username='billing2', password='x', role=SupportUser.Role.BILLING)
        ClinicAccess.objects.create(support_user=self.billing_user_a, clinic=self.clinic_a, role='viewer')

    def test_usuario_sem_acesso_a_clinica_b_nao_le_guia_de_b_por_id(self):
        self.client.force_authenticate(user=self.billing_user_a)
        resp = self.client.get(f'/api/tiss/guias/{self.guia_b.id}/')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_usuario_le_normalmente_guia_da_propria_clinica(self):
        self.client.force_authenticate(user=self.billing_user_a)
        resp = self.client.get(f'/api/tiss/guias/{self.guia_a.id}/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
