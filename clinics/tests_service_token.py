import uuid
from datetime import timedelta
import jwt
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from clinics.models import Clinic, ClinicStatus, Plan, ProvisioningStatus
from clinics.service_tokens import generate_or_load_keys, generate_service_token, decode_service_token


class ServiceTokenTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.clinic = Clinic.objects.create(
            name='Hospital Central',
            slug='hosp-central',
            plan=Plan.PROFESSIONAL,
            status=ClinicStatus.ACTIVE,
            cnpj='11.111.111/0001-11',
            provisioning_status=ProvisioningStatus.PROVISIONED,
            active_modules=['dental', 'oftalmologia'],
        )

    def test_key_generation_and_caching(self):
        """Testa se chaves RSA são geradas/carregadas e arquivos criados."""
        priv, pub = generate_or_load_keys()
        self.assertTrue(priv.startswith(b'-----BEGIN PRIVATE KEY-----') or priv.startswith(b'-----BEGIN RSA PRIVATE KEY-----'))
        self.assertTrue(pub.startswith(b'-----BEGIN PUBLIC KEY-----'))

    def test_jwt_encode_decode(self):
        """Testa codificação e decodificação do service token."""
        token = generate_service_token(
            clinic_id=str(self.clinic.id),
            plan=self.clinic.plan,
            active_modules=self.clinic.active_modules,
        )
        payload = decode_service_token(token)
        self.assertEqual(payload['clinic_id'], str(self.clinic.id))
        self.assertEqual(payload['plan'], self.clinic.plan)
        self.assertEqual(payload['active_modules'], self.clinic.active_modules)

    def test_issue_service_token_success(self):
        """Fluxo de geração de token com license key válida."""
        response = self.client.post(
            '/api/v1/auth/service-token/',
            {'license_key': str(self.clinic.license_key)},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('service_token', response.data)
        self.assertIn('expires_at', response.data)

        # Valida conteúdo do token
        payload = decode_service_token(response.data['service_token'])
        self.assertEqual(payload['clinic_id'], str(self.clinic.id))
        self.assertEqual(payload['active_modules'], ['dental', 'oftalmologia'])

    def test_issue_service_token_by_header(self):
        """Geração de token passando a licença via header X-License-Key."""
        response = self.client.post(
            '/api/v1/auth/service-token/',
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('service_token', response.data)

    def test_issue_service_token_missing_license(self):
        """Erro ao omitir licença."""
        response = self.client.post('/api/v1/auth/service-token/', format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error'], 'missing_license_key')

    def test_issue_service_token_invalid_license(self):
        """Erro com licença inexistente/inválida."""
        response = self.client.post(
            '/api/v1/auth/service-token/',
            {'license_key': str(uuid.uuid4())},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data['error'], 'invalid_license')

    def test_issue_service_token_inactive_clinic(self):
        """Clínica suspensa ou cancelada não deve obter token."""
        self.clinic.status = ClinicStatus.SUSPENDED
        self.clinic.save()

        response = self.client.post(
            '/api/v1/auth/service-token/',
            {'license_key': str(self.clinic.license_key)},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data['error'], 'clinic_suspended')

    def test_issue_service_token_expired_license(self):
        """Clínica com licença expirada não obtém token."""
        self.clinic.license_expires_at = timezone.now() - timedelta(days=1)
        self.clinic.save()

        response = self.client.post(
            '/api/v1/auth/service-token/',
            {'license_key': str(self.clinic.license_key)},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data['error'], 'license_expired')

    def test_get_license_info_authenticated(self):
        """Acesso bem-sucedido a /api/v1/license/ com service token."""
        token = generate_service_token(
            clinic_id=str(self.clinic.id),
            plan=self.clinic.plan,
            active_modules=self.clinic.active_modules,
        )

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        response = self.client.get('/api/v1/license/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['clinic_id'], str(self.clinic.id))
        self.assertEqual(response.data['clinic_name'], self.clinic.name)
        self.assertEqual(response.data['active_modules'], ['dental', 'oftalmologia'])

    def test_get_license_info_unauthenticated(self):
        """Acesso sem token retorna 401."""
        response = self.client.get('/api/v1/license/')
        # IsAuthenticated por padrão no REST_FRAMEWORK
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_license_info_invalid_token(self):
        """Acesso com token inválido retorna 401."""
        self.client.credentials(HTTP_AUTHORIZATION='Bearer invalid_jwt_token_here')
        response = self.client.get('/api/v1/license/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
