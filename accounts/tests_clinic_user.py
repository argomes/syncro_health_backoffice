"""
Testes da TASK-042a — ClinicUser + auth JWT dedicada do portal_gestor.

Cobre: login/senha, isolamento entre ClinicUser de clínicas diferentes,
clínica inativa, ClinicUser inativo, provider LDAP ainda não implementado,
e que um token de ClinicUser não dá acesso a rotas de SupportUser (e vice-versa).
"""
import uuid
from django.core.cache import cache
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase, APIClient
from rest_framework import status

from clinics.models import Clinic, ClinicStatus, Plan
from .models import ClinicUser

SupportUser = get_user_model()


def make_clinic(name='Clínica Teste', status_=ClinicStatus.ACTIVE):
    return Clinic.objects.create(
        name=name,
        slug=f'clinica-{uuid.uuid4().hex[:8]}',
        plan=Plan.PROFESSIONAL,
        status=status_,
        cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
        db_name=f'clinic_{uuid.uuid4().hex[:8]}',
        db_user=f'u_{uuid.uuid4().hex[:8]}',
    )


def make_clinic_user(clinic, email='admin@clinica.com', password='senha-forte-123', **kwargs):
    user = ClinicUser(clinic=clinic, email=email, name=kwargs.pop('name', 'Admin da Clínica'), **kwargs)
    if user.auth_provider == ClinicUser.AuthProvider.LOCAL:
        user.set_password(password)
    user.save()
    return user


class ClinicUserModelTest(TestCase):
    def setUp(self):
        self.clinic = make_clinic()

    def test_set_password_hashes_value(self):
        user = ClinicUser(clinic=self.clinic, email='a@a.com', name='A')
        user.set_password('minha-senha')
        self.assertNotEqual(user.password, 'minha-senha')
        self.assertTrue(user.check_password('minha-senha'))
        self.assertFalse(user.check_password('senha-errada'))

    def test_ldap_provider_never_checks_local_password(self):
        """LDAP é add-on pago ainda não implementado — nunca deve cair em fallback local."""
        user = ClinicUser(clinic=self.clinic, email='a@a.com', name='A', auth_provider=ClinicUser.AuthProvider.LDAP)
        user.password = ''  # nenhuma senha local foi setada
        self.assertFalse(user.check_password('qualquer-coisa'))

    def test_unique_email_per_clinic(self):
        make_clinic_user(self.clinic, email='dup@a.com')
        with self.assertRaises(Exception):
            make_clinic_user(self.clinic, email='dup@a.com')

    def test_email_normalized_to_lowercase_on_save(self):
        """
        save() normaliza para minúsculas — sem isso, 'a@x.com' e 'A@x.com'
        poderiam coexistir na mesma clínica (unique_together é case-sensitive
        no banco) e o login (que usa email__iexact) levantaria
        MultipleObjectsReturned em vez de autenticar.
        """
        user = make_clinic_user(self.clinic, email='Maiuscula@A.COM')
        user.refresh_from_db()
        self.assertEqual(user.email, 'maiuscula@a.com')

    def test_case_variant_email_blocked_by_uniqueness(self):
        make_clinic_user(self.clinic, email='dup@a.com')
        with self.assertRaises(Exception):
            make_clinic_user(self.clinic, email='DUP@A.COM')

    def test_same_email_allowed_across_different_clinics(self):
        """Duas clínicas diferentes podem ter um ClinicUser com o mesmo email."""
        other_clinic = make_clinic(name='Outra Clínica')
        make_clinic_user(self.clinic, email='mesmo@a.com')
        # Não deve levantar — unique_together é (clinic, email), não email global.
        make_clinic_user(other_clinic, email='mesmo@a.com')
        self.assertEqual(ClinicUser.objects.filter(email='mesmo@a.com').count(), 2)


class ClinicUserAuthAPITest(APITestCase):
    def setUp(self):
        # O throttle de login usa o cache padrão (locmem), que persiste entre
        # testes dentro do mesmo processo — sem isso, testes que fazem vários
        # logins seguidos estouram o limite de 10/min e recebem 429 em vez do
        # status esperado.
        cache.clear()
        self.client = APIClient()
        self.clinic_a = make_clinic(name='Clínica A')
        self.clinic_b = make_clinic(name='Clínica B')
        self.user_a = make_clinic_user(self.clinic_a, email='gerente@a.com', password='senha-a-123')
        self.user_b = make_clinic_user(self.clinic_b, email='gerente@b.com', password='senha-b-123')

    def _login(self, email, password):
        return self.client.post('/portal/api/auth/login/', {
            'email': email,
            'password': password,
        }, format='json')

    def test_login_success_returns_tokens(self):
        response = self._login('gerente@a.com', 'senha-a-123')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)

    def test_login_wrong_password_fails(self):
        response = self._login('gerente@a.com', 'senha-errada')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_unknown_email_fails(self):
        response = self._login('ninguem@x.com', 'qualquer')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_inactive_clinic_user_fails(self):
        self.user_a.is_active = False
        self.user_a.save(update_fields=['is_active'])
        response = self._login('gerente@a.com', 'senha-a-123')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_inactive_clinic_fails(self):
        self.clinic_a.status = ClinicStatus.SUSPENDED
        self.clinic_a.save(update_fields=['status'])
        response = self._login('gerente@a.com', 'senha-a-123')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_ldap_provider_rejected_with_clear_error(self):
        ldap_user = ClinicUser.objects.create(
            clinic=self.clinic_a,
            email='ldap@a.com',
            name='LDAP User',
            auth_provider=ClinicUser.AuthProvider.LDAP,
        )
        response = self._login('ldap@a.com', 'qualquer-coisa')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_me_endpoint_returns_correct_clinic_scope(self):
        login = self._login('gerente@a.com', 'senha-a-123')
        access = login.data['access']

        response = self.client.get('/portal/api/auth/me/', HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['clinic_id'], str(self.clinic_a.id))
        self.assertEqual(response.data['email'], 'gerente@a.com')

    def test_clinic_user_cannot_see_other_clinic_data(self):
        """Isolamento: token de A nunca deve resolver para o ClinicUser de B."""
        login = self._login('gerente@a.com', 'senha-a-123')
        access = login.data['access']

        response = self.client.get('/portal/api/auth/me/', HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertNotEqual(response.data['clinic_id'], str(self.clinic_b.id))
        self.assertNotEqual(response.data['id'], str(self.user_b.id))

    def test_me_endpoint_without_token_fails(self):
        response = self.client.get('/portal/api/auth/me/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_support_user_token_cannot_access_portal_me(self):
        """Token de SupportUser (equipe interna) não deve ser aceito nas rotas do portal_gestor."""
        support = SupportUser.objects.create_user(
            username='support1', email='support@syncro.com', password='pass123', role='support'
        )
        login = self.client.post('/api/auth/login/', {
            'username': 'support1', 'password': 'pass123',
        }, format='json')
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        access = login.data['access']

        response = self.client.get('/portal/api/auth/me/', HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_clinic_user_token_cannot_access_support_routes(self):
        """Token de ClinicUser não deve dar acesso às rotas internas de SupportUser."""
        login = self._login('gerente@a.com', 'senha-a-123')
        access = login.data['access']

        response = self.client.get('/api/accounts/clinic-access/', HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refresh_token_preserves_clinic_scope(self):
        login = self._login('gerente@a.com', 'senha-a-123')
        refresh = login.data['refresh']

        response = self.client.post('/portal/api/auth/refresh/', {'refresh': refresh}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        new_access = response.data['access']

        me = self.client.get('/portal/api/auth/me/', HTTP_AUTHORIZATION=f'Bearer {new_access}')
        self.assertEqual(me.status_code, status.HTTP_200_OK)
        self.assertEqual(me.data['clinic_id'], str(self.clinic_a.id))
