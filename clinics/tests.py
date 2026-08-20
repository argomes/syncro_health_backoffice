import uuid
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from .models import Clinic, ClinicStatus, Plan, ProvisioningStatus


def make_clinic(name='Clínica Teste', clinic_status=ClinicStatus.ACTIVE, **kwargs):
    return Clinic.objects.create(
        name=name,
        slug=f'clinica-{uuid.uuid4().hex[:8]}',
        plan=Plan.PROFESSIONAL,
        status=clinic_status,
        cnpj='00.000.000/0001-00',
        cnes='1234567',
        db_name=f'clinic_{uuid.uuid4().hex[:8]}',
        provisioning_status=ProvisioningStatus.PROVISIONED,
        **kwargs,
    )


class ValidateLicenseTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = '/api/clinics/validate-license/'

    def test_active_clinic_returns_200(self):
        clinic = make_clinic(name='Clínica São Lucas')
        response = self.client.post(
            self.url,
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['status'], 'active')
        self.assertEqual(data['plan'], 'professional')
        self.assertEqual(data['clinic_name'], 'Clínica São Lucas')
        self.assertEqual(data['cnpj'], '00.000.000/0001-00')
        self.assertEqual(data['cnes'], '1234567')
        self.assertEqual(data['db_name'], clinic.db_name)
        self.assertEqual(data['provisioning_status'], 'provisioned')

    def test_suspended_clinic_returns_402(self):
        clinic = make_clinic(clinic_status=ClinicStatus.SUSPENDED)
        response = self.client.post(
            self.url,
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        data = response.json()
        self.assertEqual(data['error'], 'license_suspended')
        self.assertEqual(data['status'], 'suspended')

    def test_cancelled_clinic_returns_403(self):
        clinic = make_clinic(clinic_status=ClinicStatus.CANCELLED)
        response = self.client.post(
            self.url,
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        data = response.json()
        self.assertEqual(data['error'], 'license_cancelled')
        self.assertEqual(data['status'], 'cancelled')

    def test_missing_license_key_returns_401(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_license_key_returns_401(self):
        response = self.client.post(
            self.url,
            HTTP_X_LICENSE_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_active_clinic_without_cnes_returns_blank_string(self):
        """
        Clínica migrada antes deste campo existir (ex.: Ambar) não tem CNES
        cadastrado ainda — o endpoint deve devolver string vazia, não
        quebrar/omitir a chave, para o gateway distinguir "não preenchido"
        de "campo ausente do contrato".
        """
        clinic = Clinic.objects.create(
            name='Clínica Sem CNES',
            slug=f'clinica-{uuid.uuid4().hex[:8]}',
            plan=Plan.PROFESSIONAL,
            status=ClinicStatus.ACTIVE,
            cnpj=f'{uuid.uuid4().hex[:14]}',
            db_name=f'clinic_{uuid.uuid4().hex[:8]}',
            provisioning_status=ProvisioningStatus.PROVISIONED,
        )
        response = self.client.post(
            self.url,
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['cnes'], '')


class ClinicCnesFieldTests(TestCase):
    """Cobertura de model para o campo `cnes` (EDGW-add-cnes-field)."""

    def test_cnes_is_blank_by_default(self):
        clinic = Clinic.objects.create(
            name='Clínica Default',
            slug=f'clinica-{uuid.uuid4().hex[:8]}',
            cnpj=f'{uuid.uuid4().hex[:14]}',
        )
        self.assertEqual(clinic.cnes, '')

    def test_cnes_accepts_seven_digit_code(self):
        clinic = Clinic.objects.create(
            name='Clínica Com CNES',
            slug=f'clinica-{uuid.uuid4().hex[:8]}',
            cnpj=f'{uuid.uuid4().hex[:14]}',
            cnes='2077469',
        )
        clinic.refresh_from_db()
        self.assertEqual(clinic.cnes, '2077469')

    def test_cnes_is_not_unique_constrained(self):
        """
        Ao contrário de cnpj, cnes não tem constraint de unicidade no banco
        — duas clínicas cadastradas manualmente antes de checagem podem
        temporariamente compartilhar o mesmo valor sem erro de integridade.
        """
        shared_cnes = '9999999'
        Clinic.objects.create(
            name='Clínica A', slug=f'clinica-{uuid.uuid4().hex[:8]}',
            cnpj=f'{uuid.uuid4().hex[:14]}', cnes=shared_cnes,
            db_name=f'clinic_{uuid.uuid4().hex[:8]}', db_user=f'u_{uuid.uuid4().hex[:8]}',
        )
        clinic_b = Clinic.objects.create(
            name='Clínica B', slug=f'clinica-{uuid.uuid4().hex[:8]}',
            cnpj=f'{uuid.uuid4().hex[:14]}', cnes=shared_cnes,
            db_name=f'clinic_{uuid.uuid4().hex[:8]}', db_user=f'u_{uuid.uuid4().hex[:8]}',
        )
        self.assertEqual(clinic_b.cnes, shared_cnes)
