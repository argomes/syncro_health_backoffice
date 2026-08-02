"""
Testes de BUGFIX-24 (QABUG-REMOTESUPPORT-001) — endpoint
POST /api/clinics/db-access-revoke/.

O grant de BACFF-AVULSA-06 vive só em cache (Redis), sem role/conexão
Postgres real criada para a concessão — revogar é remover a chave do cache.
Cobre: revogação de grant ativo, idempotência sem grant prévio, guard de
license_key ausente, isolamento entre clínicas.
"""
import uuid

from django.core.cache import cache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from .models import Clinic, ClinicStatus, Plan, ProvisioningStatus


def make_clinic(clinic_status=ClinicStatus.ACTIVE, provisioning_status=ProvisioningStatus.PROVISIONED, **kwargs):
    return Clinic.objects.create(
        name='Clínica Teste',
        slug=f'clinica-{uuid.uuid4().hex[:8]}',
        plan=Plan.PROFESSIONAL,
        status=clinic_status,
        cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
        db_name=f'clinic_{uuid.uuid4().hex[:8]}',
        db_user=f'u_{uuid.uuid4().hex[:8]}',
        provisioning_status=provisioning_status,
        **kwargs,
    )


class DbAccessRevokeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.grant_url = '/api/clinics/db-access-grant/'
        self.revoke_url = '/api/clinics/db-access-revoke/'

    def test_revoke_active_grant_returns_200_and_clears_cache(self):
        clinic = make_clinic()
        self.client.post(
            self.grant_url,
            {'db_password': 's3nh4-temp', 'granted_by': 'admin-local'},
            format='json',
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )
        self.assertIsNotNone(cache.get(f"clinic_db_grant:{clinic.id}"))

        response = self.client.post(
            self.revoke_url,
            {},
            format='json',
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.json()['revoked'])
        self.assertIsNone(cache.get(f"clinic_db_grant:{clinic.id}"))

    def test_revoke_without_prior_grant_is_idempotent(self):
        clinic = make_clinic()
        response = self.client.post(
            self.revoke_url,
            {},
            format='json',
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.json()['revoked'])

    def test_missing_license_key_returns_401(self):
        response = self.client.post(self.revoke_url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_revoke_only_clears_target_clinic_grant(self):
        clinic_a = make_clinic()
        clinic_b = make_clinic()
        self.client.post(
            self.grant_url,
            {'db_password': 'senha-a', 'granted_by': 'admin-a'},
            format='json',
            HTTP_X_LICENSE_KEY=str(clinic_a.license_key),
        )
        self.client.post(
            self.grant_url,
            {'db_password': 'senha-b', 'granted_by': 'admin-b'},
            format='json',
            HTTP_X_LICENSE_KEY=str(clinic_b.license_key),
        )

        self.client.post(
            self.revoke_url,
            {},
            format='json',
            HTTP_X_LICENSE_KEY=str(clinic_a.license_key),
        )

        self.assertIsNone(cache.get(f"clinic_db_grant:{clinic_a.id}"))
        self.assertIsNotNone(cache.get(f"clinic_db_grant:{clinic_b.id}"))
