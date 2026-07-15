"""
Testes da TASK-046 — login/logout/refresh via cookie httpOnly para as páginas
server-rendered do portal_gestor.
"""
import uuid

from django.test import TestCase

from accounts.authentication import PORTAL_ACCESS_COOKIE
from accounts.models import ClinicUser
from clinics.models import Clinic, ClinicStatus, Plan

from .template_views import PORTAL_REFRESH_COOKIE


def make_clinic(name='Clínica Teste'):
    return Clinic.objects.create(
        name=name,
        slug=f'clinica-{uuid.uuid4().hex[:8]}',
        plan=Plan.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
        db_name=f'clinic_{uuid.uuid4().hex[:8]}',
        db_user=f'u_{uuid.uuid4().hex[:8]}',
    )


def make_clinic_user(clinic, email='gerente@a.com', password='senha-123'):
    user = ClinicUser(clinic=clinic, email=email, name='Gerente')
    user.set_password(password)
    user.save()
    return user


class PortalLoginViewTest(TestCase):
    def setUp(self):
        self.clinic = make_clinic()
        self.user = make_clinic_user(self.clinic)

    def test_login_page_renders(self):
        response = self.client.get('/portal/login/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Entrar')

    def test_login_success_sets_cookies_and_redirects(self):
        response = self.client.post('/portal/login/', {
            'email': 'gerente@a.com', 'password': 'senha-123',
        })
        self.assertRedirects(response, '/portal/', fetch_redirect_response=False)
        self.assertIn(PORTAL_ACCESS_COOKIE, response.cookies)
        self.assertIn(PORTAL_REFRESH_COOKIE, response.cookies)
        self.assertTrue(response.cookies[PORTAL_ACCESS_COOKIE]['httponly'])
        self.assertTrue(response.cookies[PORTAL_REFRESH_COOKIE]['httponly'])

    def test_login_wrong_password_shows_error_no_cookies(self):
        response = self.client.post('/portal/login/', {
            'email': 'gerente@a.com', 'password': 'senha-errada',
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'inválidos', status_code=400)
        self.assertNotIn(PORTAL_ACCESS_COOKIE, response.cookies)

    def test_login_redirects_to_next_param(self):
        response = self.client.post('/portal/login/?next=/portal/relatorios/', {
            'email': 'gerente@a.com', 'password': 'senha-123', 'next': '/portal/relatorios/',
        })
        self.assertRedirects(response, '/portal/relatorios/', fetch_redirect_response=False)

    def test_login_rejects_external_next_open_redirect(self):
        response = self.client.post('/portal/login/', {
            'email': 'gerente@a.com', 'password': 'senha-123',
            'next': 'https://evil.example/phish',
        })
        self.assertRedirects(response, '/portal/', fetch_redirect_response=False)

    def test_login_rejects_protocol_relative_next(self):
        response = self.client.post('/portal/login/', {
            'email': 'gerente@a.com', 'password': 'senha-123',
            'next': '//evil.example/phish',
        })
        self.assertRedirects(response, '/portal/', fetch_redirect_response=False)

    def test_login_page_get_ignores_unsafe_next_in_form_action(self):
        response = self.client.get('/portal/login/?next=https://evil.example/phish')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'evil.example')

    def test_login_inactive_clinic_user_rejected(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])

        response = self.client.post('/portal/login/', {
            'email': 'gerente@a.com', 'password': 'senha-123',
        })
        self.assertEqual(response.status_code, 400)


class PortalCookieAuthenticatedAccessTest(TestCase):
    """Prova que ClinicPortalAuthMiddleware (TASK-047) autentica corretamente
    uma view HTML "pura" a partir do cookie e injeta request.clinic_user/
    request.clinic — a stub em /portal/ existe precisamente para isso
    (será substituída pelo dashboard real na TASK-049)."""

    def setUp(self):
        self.clinic_a = make_clinic('Clínica A')
        self.clinic_b = make_clinic('Clínica B')
        self.user_a = make_clinic_user(self.clinic_a, email='gerente@a.com')
        self.user_b = make_clinic_user(self.clinic_b, email='gerente@b.com')

    def _login(self, email='gerente@a.com', password='senha-123'):
        self.client.post('/portal/login/', {'email': email, 'password': password})

    def test_protected_view_without_cookie_redirects_to_login(self):
        response = self.client.get('/portal/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/portal/login/'))

    def test_protected_view_with_valid_cookie_authenticates(self):
        self._login()
        response = self.client.get('/portal/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'gerente@a.com', response.content)
        self.assertIn('Clínica A'.encode(), response.content)

    def test_protected_view_scoped_to_own_clinic(self):
        self._login(email='gerente@b.com')
        response = self.client.get('/portal/')
        self.assertIn('Clínica B'.encode(), response.content)
        self.assertNotIn('Clínica A'.encode(), response.content)

    def test_garbage_cookie_redirects_to_login_not_500(self):
        self.client.cookies[PORTAL_ACCESS_COOKIE] = 'not-a-real-jwt'
        response = self.client.get('/portal/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/portal/login/'))


class PortalLogoutViewTest(TestCase):
    def setUp(self):
        self.clinic = make_clinic()
        make_clinic_user(self.clinic)
        self.client.post('/portal/login/', {'email': 'gerente@a.com', 'password': 'senha-123'})

    def test_logout_clears_cookies_and_blocks_access(self):
        response = self.client.post('/portal/logout/')
        self.assertRedirects(response, '/portal/login/', fetch_redirect_response=False)
        self.assertEqual(response.cookies[PORTAL_ACCESS_COOKIE].value, '')

        protected = self.client.get('/portal/')
        self.assertEqual(protected.status_code, 302)
        self.assertTrue(protected.url.startswith('/portal/login/'))


class PortalRefreshViewTest(TestCase):
    def setUp(self):
        self.clinic = make_clinic()
        make_clinic_user(self.clinic)
        self.client.post('/portal/login/', {'email': 'gerente@a.com', 'password': 'senha-123'})

    def test_refresh_without_cookie_redirects_to_login(self):
        self.client.cookies.pop(PORTAL_REFRESH_COOKIE, None)
        response = self.client.post('/portal/refresh/')
        self.assertRedirects(response, '/portal/login/', fetch_redirect_response=False)

    def test_refresh_with_garbage_cookie_redirects_to_login_and_clears(self):
        self.client.cookies[PORTAL_REFRESH_COOKIE] = 'invalido'
        response = self.client.post('/portal/refresh/')
        self.assertRedirects(response, '/portal/login/', fetch_redirect_response=False)
        self.assertEqual(response.cookies[PORTAL_ACCESS_COOKIE].value, '')

    def test_refresh_with_valid_cookie_issues_new_access(self):
        response = self.client.post('/portal/refresh/')
        self.assertRedirects(response, '/portal/', fetch_redirect_response=False)
        self.assertIn(PORTAL_ACCESS_COOKIE, response.cookies)

        # O novo access cookie continua autenticando normalmente.
        protected = self.client.get('/portal/')
        self.assertEqual(protected.status_code, 200)

    def test_refresh_rejects_external_next_open_redirect(self):
        response = self.client.post('/portal/refresh/', {'next': 'https://evil.example/phish'})
        self.assertRedirects(response, '/portal/', fetch_redirect_response=False)
