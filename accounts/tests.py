import uuid
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken

from clinics.models import Clinic, ClinicStatus, Plan
from .models import ClinicAccess

User = get_user_model()


class SupportUserLogoutTest(APITestCase):
    """BACFF-004: logout revoga o refresh token via blacklist."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='logout_user',
            email='logout@test.com',
            password='logout123',
            role='admin',
        )

    def test_logout_blacklists_refresh_token(self):
        refresh = RefreshToken.for_user(self.user)
        refresh_str = str(refresh)

        response = self.client.post(
            '/api/auth/logout/', {'refresh': refresh_str}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_205_RESET_CONTENT)

        # Refresh token usado novamente (ex.: para obter novo access token)
        # deve falhar, pois foi adicionado à blacklist.
        refresh_response = self.client.post(
            '/api/auth/refresh/', {'refresh': refresh_str}, format='json'
        )
        self.assertEqual(refresh_response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_missing_refresh_token_returns_400(self):
        response = self.client.post('/api/auth/logout/', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_logout_invalid_refresh_token_returns_401(self):
        response = self.client.post(
            '/api/auth/logout/', {'refresh': 'not-a-valid-token'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ClinicAccessModelTest(TestCase):
    """Testes unitários do modelo ClinicAccess."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin',
            email='admin@test.com',
            password='pass123',
            role='admin'
        )
        self.support = User.objects.create_user(
            username='support',
            email='support@test.com',
            password='pass123',
            role='support'
        )
        self.clinic = Clinic.objects.create(
            name='Clínica Teste',
            slug='clinica-teste',
            plan='starter',
            status='active',
            cnpj='12.345.678/0001-99',
            db_name='clinic_test',
            db_user='u_test'
        )

    def test_create_clinic_access(self):
        """Criar acesso de clínica."""
        access = ClinicAccess.objects.create(
            support_user=self.support,
            clinic=self.clinic,
            role='viewer',
            granted_by=self.admin
        )
        self.assertEqual(access.support_user, self.support)
        self.assertEqual(access.clinic, self.clinic)
        self.assertTrue(access.is_active())

    def test_clinic_access_uniqueness(self):
        """User não pode ter dois acessos à mesma clínica."""
        ClinicAccess.objects.create(
            support_user=self.support,
            clinic=self.clinic,
            role='viewer',
            granted_by=self.admin
        )

        with self.assertRaises(Exception):
            ClinicAccess.objects.create(
                support_user=self.support,
                clinic=self.clinic,
                role='admin',
                granted_by=self.admin
            )

    def test_is_active_method(self):
        """Método is_active() retorna status correto."""
        access = ClinicAccess.objects.create(
            support_user=self.support,
            clinic=self.clinic,
            role='viewer',
            granted_by=self.admin
        )
        self.assertTrue(access.is_active())

        from django.utils import timezone
        access.revoked_at = timezone.now()
        access.save()
        self.assertFalse(access.is_active())

    def test_admin_string_representation(self):
        """String representation do acesso."""
        access = ClinicAccess.objects.create(
            support_user=self.support,
            clinic=self.clinic,
            role='viewer',
            granted_by=self.admin
        )
        self.assertIn('support', str(access))
        self.assertIn('Clínica Teste', str(access))


class ClinicAccessAPITest(APITestCase):
    """Testes E2E da API de ClinicAccess."""

    def setUp(self):
        self.client = APIClient()

        # Criar usuários
        self.admin_user = User.objects.create_user(
            username='admin_user',
            email='admin@test.com',
            password='admin123',
            role='admin'
        )
        self.support_user = User.objects.create_user(
            username='support_user',
            email='support@test.com',
            password='support123',
            role='support'
        )
        self.billing_user = User.objects.create_user(
            username='billing_user',
            email='billing@test.com',
            password='billing123',
            role='billing'
        )

        # Criar clínicas
        self.clinic1 = Clinic.objects.create(
            name='Clínica São Paulo',
            slug='clinica-sp',
            plan='professional',
            status='active',
            cnpj='11.111.111/0001-11',
            db_name='clinic_sp',
            db_user='u_sp'
        )
        self.clinic2 = Clinic.objects.create(
            name='Clínica Rio de Janeiro',
            slug='clinica-rj',
            plan='professional',
            status='active',
            cnpj='22.222.222/0001-22',
            db_name='clinic_rj',
            db_user='u_rj'
        )

    def test_create_clinic_access(self):
        """POST /api/accounts/clinic-access/ — criar acesso."""
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.post('/api/accounts/clinic-access/', {
            'support_user': self.support_user.id,
            'clinic': self.clinic1.id,
            'role': 'admin',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['role'], 'admin')

    def test_list_clinic_access(self):
        """GET /api/accounts/clinic-access/ — listar acessos."""
        ClinicAccess.objects.create(
            support_user=self.support_user,
            clinic=self.clinic1,
            role='viewer',
            granted_by=self.admin_user
        )
        ClinicAccess.objects.create(
            support_user=self.billing_user,
            clinic=self.clinic2,
            role='admin',
            granted_by=self.admin_user
        )

        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/accounts/clinic-access/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data['results']), 2)

    def test_support_user_sees_only_own_access(self):
        """Support user vê apenas seus próprios acessos."""
        ClinicAccess.objects.create(
            support_user=self.support_user,
            clinic=self.clinic1,
            role='viewer',
            granted_by=self.admin_user
        )
        ClinicAccess.objects.create(
            support_user=self.billing_user,
            clinic=self.clinic2,
            role='admin',
            granted_by=self.admin_user
        )

        self.client.force_authenticate(user=self.support_user)
        response = self.client.get('/api/accounts/clinic-access/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)

    def test_revoke_clinic_access(self):
        """POST /api/accounts/clinic-access/:id/revoke/ — revogar acesso."""
        access = ClinicAccess.objects.create(
            support_user=self.support_user,
            clinic=self.clinic1,
            role='viewer',
            granted_by=self.admin_user
        )

        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(
            f'/api/accounts/clinic-access/{access.id}/revoke/'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        access.refresh_from_db()
        self.assertIsNotNone(access.revoked_at)

    def test_by_clinic_endpoint(self):
        """GET /api/accounts/clinic-access/by-clinic/{clinic_id}/ — admins de uma clínica."""
        ClinicAccess.objects.create(
            support_user=self.support_user,
            clinic=self.clinic1,
            role='admin',
            granted_by=self.admin_user
        )

        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(
            f'/api/accounts/clinic-access/by-clinic/{self.clinic1.id}/'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data['total'], 1)

    def test_my_clinics_endpoint(self):
        """GET /api/accounts/clinic-access/my-clinics/ — minhas clínicas."""
        ClinicAccess.objects.create(
            support_user=self.support_user,
            clinic=self.clinic1,
            role='admin',
            granted_by=self.admin_user
        )

        self.client.force_authenticate(user=self.support_user)
        response = self.client.get('/api/accounts/clinic-access/my-clinics/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data['total'], 1)

    def test_update_clinic_access_role(self):
        """PATCH /api/accounts/clinic-access/:id/ — atualizar role."""
        access = ClinicAccess.objects.create(
            support_user=self.support_user,
            clinic=self.clinic1,
            role='viewer',
            granted_by=self.admin_user
        )

        self.client.force_authenticate(user=self.admin_user)
        response = self.client.patch(
            f'/api/accounts/clinic-access/{access.id}/',
            {'role': 'admin'},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_role_escalation_denied_for_unknown_role(self):
        """
        BACFF-002: se o role do criador (ou o role solicitado) não estiver
        em _ACCESS_ROLE_RANK — por corrupção de dado legado ou um valor fora
        do enum — o helper _get_role_rank deve levantar PermissionDenied em
        vez de tratá-lo silenciosamente como rank 0 (viewer), que abriria uma
        via de bypass da checagem de escalação de privilégio.
        """
        # support_user tem acesso a clinic1, mas com um role corrompido/desconhecido
        # (bypassa a validação do serializer usando update() no queryset).
        ClinicAccess.objects.create(
            support_user=self.support_user,
            clinic=self.clinic1,
            role='viewer',
            granted_by=self.admin_user,
        )
        ClinicAccess.objects.filter(
            support_user=self.support_user, clinic=self.clinic1
        ).update(role='legacy_super_role')

        self.client.force_authenticate(user=self.support_user)
        response = self.client.post('/api/accounts/clinic-access/', {
            'support_user': self.billing_user.id,
            'clinic': self.clinic1.id,
            'role': 'viewer',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_access_fails(self):
        """Acesso sem autenticação retorna 401."""
        response = self.client.get('/api/accounts/clinic-access/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_by_clinic_denied_without_access(self):
        """BO-SEC-004: support user sem acesso à clínica recebe 403 em by-clinic."""
        # support_user não tem ClinicAccess a clinic1
        self.client.force_authenticate(user=self.support_user)
        response = self.client.get(
            f'/api/accounts/clinic-access/by-clinic/{self.clinic1.id}/'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_by_clinic_allowed_with_own_access(self):
        """Support user com ClinicAccess ativo à clínica consegue consultar by-clinic."""
        ClinicAccess.objects.create(
            support_user=self.support_user,
            clinic=self.clinic1,
            role='viewer',
            granted_by=self.admin_user,
        )
        self.client.force_authenticate(user=self.support_user)
        response = self.client.get(
            f'/api/accounts/clinic-access/by-clinic/{self.clinic1.id}/'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_by_clinic_allowed_for_admin(self):
        """Usuário ADMIN acessa by-clinic de qualquer clínica sem restrição."""
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(
            f'/api/accounts/clinic-access/by-clinic/{self.clinic2.id}/'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_by_user_denied_for_other_user(self):
        """BO-SEC-004: support user não pode consultar by-user de outro usuário."""
        self.client.force_authenticate(user=self.support_user)
        response = self.client.get(
            f'/api/accounts/clinic-access/by-user/{self.billing_user.id}/'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_by_user_allowed_for_self(self):
        """Support user consegue consultar seus próprios dados via by-user."""
        self.client.force_authenticate(user=self.support_user)
        response = self.client.get(
            f'/api/accounts/clinic-access/by-user/{self.support_user.id}/'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_by_user_allowed_for_admin(self):
        """Usuário ADMIN acessa by-user de qualquer usuário sem restrição."""
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(
            f'/api/accounts/clinic-access/by-user/{self.billing_user.id}/'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
