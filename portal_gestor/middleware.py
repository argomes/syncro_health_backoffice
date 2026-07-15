"""
TASK-047 — protege as páginas server-rendered do portal_gestor.

As rotas JSON (/portal/api/*) já se autenticam sozinhas via
authentication_classes=[ClinicJWTAuthentication/ClinicCookieJWTAuthentication]
nas views DRF — este middleware NÃO deve tocar nelas (dupla proteção
desnecessária, e views DRF/APIView têm seu próprio ciclo de erro em JSON).
Ele existe só para as views HTML "puras" (Django View/TemplateView) das
próximas tasks (TASK-049/050 em diante), que não têm authentication_classes.
"""
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import urlencode
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.exceptions import TokenError

from accounts.authentication import ClinicCookieJWTAuthentication

# Prefixos sob /portal/ que NÃO passam por este gate — login/logout/refresh
# precisam ser acessíveis sem estar autenticado (senão ninguém consegue logar),
# e /portal/api/ já tem seu próprio mecanismo de auth via DRF.
#
# IMPORTANTE: cada prefixo termina com '/' de propósito. Um `path.startswith`
# sem essa barra faria uma rota futura tipo `/portal/loginX/` casar
# acidentalmente com `/portal/login` e vazar pelo gate sem autenticação —
# a barra final funciona como delimitador de borda de segmento de path.
_EXEMPT_PREFIXES = (
    '/portal/login/',
    '/portal/logout/',
    '/portal/refresh/',
    '/portal/api/',
    # TASK-BO-12 — "esqueci minha senha" precisa ser acessível sem sessão
    # válida (é justamente pra quem não consegue logar); mesmo raciocínio
    # de /portal/login/ acima.
    '/portal/password-reset/',
)


class ClinicPortalAuthMiddleware:
    """
    Para qualquer request sob /portal/ (exceto os prefixos isentos acima):
    decodifica o cookie JWT do ClinicUser e injeta `request.clinic_user`/
    `request.clinic`, ou redireciona para /portal/login/?next=... se
    ausente/inválido/expirado.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._auth = ClinicCookieJWTAuthentication()

    def __call__(self, request):
        if self._requires_auth(request.path):
            result = self._try_authenticate(request)
            if result is None:
                return self._redirect_to_login(request)
            request.clinic_user, request.auth_token = result
            request.clinic = request.clinic_user.clinic

        return self.get_response(request)

    @staticmethod
    def _requires_auth(path: str) -> bool:
        return path.startswith('/portal/') and not path.startswith(_EXEMPT_PREFIXES)

    def _try_authenticate(self, request):
        try:
            return self._auth.authenticate(request)
        except (AuthenticationFailed, TokenError):
            return None

    @staticmethod
    def _redirect_to_login(request):
        login_url = reverse('portal_login')
        query = urlencode({'next': request.get_full_path()})
        return redirect(f'{login_url}?{query}')
