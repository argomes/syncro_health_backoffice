import uuid

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from clinics.models import Clinic, ClinicStatus, Plan, ProvisioningStatus
from .models import TUSSProcedureCode, ANSInsuranceOperator


def make_clinic():
    unique = uuid.uuid4().hex[:8]
    return Clinic.objects.create(
        name='Clínica Referência Teste',
        slug=f'clinica-ref-{unique}',
        plan=Plan.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj=f'12.345.{unique[:3]}/0001-99',
        db_name=f'db_{unique}',
        db_user=f'u_{unique}',
        provisioning_status=ProvisioningStatus.PROVISIONED,
    )


class ReferenceDataEndpointsTests(TestCase):
    """
    EDGW-013 — endpoints consumidos pelo Edge Gateway em cache-aside (busca
    local primeiro; só bate aqui em caso de miss). Mesmo padrão de auth de
    tests_elegibilidade.py (license_key no header).
    """

    def setUp(self):
        self.client = APIClient()
        self.clinic = make_clinic()
        TUSSProcedureCode.objects.update_or_create(
            tuss_code='81000030', defaults={'description': 'Consulta odontológica', 'table_code': '90'},
        )
        ANSInsuranceOperator.objects.update_or_create(
            ans_code='301949', defaults={'name': 'Odontoprev', 'cnpj': '58119199000151'},
        )

    def test_procedure_code_lookup_exige_license_key(self):
        resp = self.client.get('/api/tiss/reference/procedure-codes/81000030/')
        self.assertEqual(resp.status_code, 401)

    def test_procedure_code_lookup_encontrado(self):
        resp = self.client.get(
            '/api/tiss/reference/procedure-codes/81000030/',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['description'], 'Consulta odontológica')

    def test_procedure_code_lookup_nao_encontrado(self):
        resp = self.client.get(
            '/api/tiss/reference/procedure-codes/00000000/',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(resp.status_code, 404)

    def test_procedure_code_search_por_codigo_exato(self):
        resp = self.client.get(
            '/api/tiss/reference/procedure-codes/search/?q=81000030',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(resp.status_code, 200)
        codes = [c['tuss_code'] for c in resp.data]
        self.assertIn('81000030', codes)

    def test_procedure_code_search_sem_query_retorna_400(self):
        resp = self.client.get(
            '/api/tiss/reference/procedure-codes/search/',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(resp.status_code, 400)

    def test_insurance_operator_lookup_encontrado(self):
        resp = self.client.get(
            '/api/tiss/reference/operators/301949/',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['name'], 'Odontoprev')

    def test_insurance_operator_search_por_nome(self):
        resp = self.client.get(
            '/api/tiss/reference/operators/search/?q=odonto',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['ans_code'], '301949')


class ReferenceDataThrottleTests(TestCase):
    """
    BACFF-AVULSA-03 (2026-07-20): os 4 endpoints de referência TUSS/ANS
    dependiam só do AnonRateThrottle default (60/min por IP), insuficiente
    para diferenciar uso legítimo do gateway (cache-aside) de scraping
    fatiado da tabela pública TUSS/ANS. Agora usam throttle dedicado
    (ReferenceDataRateThrottle, scope='reference_data').

    NOTA: assim como em clinics/tests_db_access_grant.py, a rate real de
    produção é lida dinamicamente de settings.py — SimpleRateThrottle lê
    THROTTLE_RATES de api_settings no import do módulo, então
    override_settings(REST_FRAMEWORK=...) em tempo de teste não teria efeito.
    """

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.clinic = make_clinic()
        TUSSProcedureCode.objects.update_or_create(
            tuss_code='81000030', defaults={'description': 'Consulta odontológica', 'table_code': '90'},
        )
        rate = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['reference_data']
        num_requests, period = rate.split('/')
        assert period == 'minute', "teste assume rate por minuto; ajustar se a unidade mudar"
        self.limit = int(num_requests)

    def _lookup(self):
        return self.client.get(
            '/api/tiss/reference/procedure-codes/81000030/',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )

    def test_calls_within_limit_succeed(self):
        for _ in range(self.limit):
            self.assertEqual(self._lookup().status_code, status.HTTP_200_OK)

    def test_call_beyond_limit_returns_429(self):
        for _ in range(self.limit):
            self.assertEqual(self._lookup().status_code, status.HTTP_200_OK)

        response = self._lookup()
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_search_endpoint_shares_same_throttle_scope(self):
        # procedure_code_search compartilha o mesmo scope (reference_data) e
        # o mesmo balde por IP que procedure_code_lookup.
        for _ in range(self.limit):
            self.assertEqual(self._lookup().status_code, status.HTTP_200_OK)

        response = self.client.get(
            '/api/tiss/reference/procedure-codes/search/?q=81000030',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
