import uuid

from django.test import TestCase
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
