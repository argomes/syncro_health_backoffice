"""
Testes da TASK-047 — ClinicPortalAuthMiddleware: protege /portal/* (exceto
login/logout/refresh/api), injeta request.clinic_user/request.clinic, e não
interfere em rotas fora de /portal/.
"""
import uuid
from urllib.parse import parse_qs, urlparse

from django.test import TestCase

from accounts.authentication import PORTAL_ACCESS_COOKIE
from accounts.models import ClinicUser
from clinics.models import Clinic, ClinicStatus, Plan


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


class ClinicPortalAuthMiddlewareTest(TestCase):
    def setUp(self):
        self.clinic = make_clinic()
        make_clinic_user(self.clinic)

    def test_exempt_login_route_accessible_without_cookie(self):
        response = self.client.get('/portal/login/')
        self.assertEqual(response.status_code, 200)

    def test_exempt_api_route_not_redirected_by_middleware(self):
        """
        /portal/api/... já tem seu próprio mecanismo de auth (DRF
        authentication_classes) — o middleware não deve interceptar antes,
        senão a resposta viraria um redirect HTML em vez do 401 JSON esperado
        pelos consumidores de API.
        """
        response = self.client.post('/portal/api/reports/sessions/', {}, content_type='application/json')
        self.assertNotEqual(response.status_code, 302)
        self.assertEqual(response.status_code, 401)

    def test_non_portal_routes_unaffected(self):
        response = self.client.get('/api/metrics/heartbeat')
        # Sem X-License-Key isso é 403/401 vindo da própria view, nunca um
        # redirect de login do portal — prova que o middleware não tocou.
        self.assertNotEqual(response.status_code, 302)

    def test_admin_route_unaffected_by_portal_middleware(self):
        response = self.client.get('/admin/login/')
        self.assertNotIn('/portal/login/', response.get('Location', ''))

    def test_redirect_preserves_next_with_full_path(self):
        response = self.client.get('/portal/?foo=bar')
        self.assertEqual(response.status_code, 302)

        parsed = urlparse(response.url)
        self.assertEqual(parsed.path, '/portal/login/')
        next_value = parse_qs(parsed.query)['next'][0]
        self.assertEqual(next_value, '/portal/?foo=bar')

    def test_middleware_injects_clinic_user_and_clinic(self):
        self.client.post('/portal/login/', {'email': 'gerente@a.com', 'password': 'senha-123'})
        response = self.client.get('/portal/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.clinic.name.encode(), response.content)

    def test_expired_or_malformed_cookie_does_not_crash_exempt_routes(self):
        self.client.cookies[PORTAL_ACCESS_COOKIE] = 'garbage'
        response = self.client.get('/portal/login/')
        self.assertEqual(response.status_code, 200)

    def test_prefix_match_does_not_leak_via_startswith_without_boundary(self):
        """
        Regressão: _EXEMPT_PREFIXES usa `path.startswith(tupla)`. Sem a barra
        final em cada prefixo, uma rota futura tipo `/portal/loginX/` (que não
        é o login de verdade) casaria acidentalmente com o prefixo
        `/portal/login` e vazaria pelo gate de autenticação sem querer. Os
        prefixos devem SEMPRE terminar com '/' — este teste exercita
        `_requires_auth` diretamente (sem depender de uma URL real existir)
        para travar essa garantia.
        """
        from portal_gestor.middleware import ClinicPortalAuthMiddleware

        self.assertTrue(ClinicPortalAuthMiddleware._requires_auth('/portal/loginX/'))
        self.assertTrue(ClinicPortalAuthMiddleware._requires_auth('/portal/logoutz'))
        self.assertTrue(ClinicPortalAuthMiddleware._requires_auth('/portal/apiary/'))
        # Os prefixos legítimos (com barra final) continuam isentos.
        self.assertFalse(ClinicPortalAuthMiddleware._requires_auth('/portal/login/'))
        self.assertFalse(ClinicPortalAuthMiddleware._requires_auth('/portal/api/reports/'))
