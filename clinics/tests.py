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
