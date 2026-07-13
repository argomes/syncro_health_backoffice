from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, TokenError

from .models import ClinicUser

# Claim usado para distinguir tokens de ClinicUser de tokens de SupportUser —
# sem isso, um token de SupportUser (mesmo formato JWT) poderia ser aceito aqui
# e vice-versa, já que os dois usam a mesma SECRET_KEY/algoritmo.
CLINIC_USER_TOKEN_SCOPE = 'clinic_user'


class SafeJWTAuthentication(JWTAuthentication):
    """
    JWTAuthentication padrão, endurecida contra tokens com user_id de tipo
    incompatível com o AUTH_USER_MODEL. Ex.: um token de ClinicUser (user_id
    é UUID) apresentado numa rota autenticada para SupportUser (id é inteiro)
    — sem este wrapper, o get_user() padrão do simplejwt deixa vazar um
    ValueError/django ValidationError como 500 em vez de 401.

    Usada como DEFAULT_AUTHENTICATION_CLASSES global (settings.py) no lugar de
    JWTAuthentication puro, precisamente porque a introdução do ClinicUser
    (TASK-042a) faz duas famílias de token com formatos de id diferentes
    coexistirem sob o mesmo header Authorization.
    """

    def get_user(self, validated_token):
        try:
            return super().get_user(validated_token)
        except (ValueError, TypeError) as exc:
            raise AuthenticationFailed('invalid_user_id_type', code='token_not_valid') from exc


class ClinicJWTAuthentication(JWTAuthentication):
    """
    Autenticação JWT dedicada para ClinicUser — usada apenas nas views do
    portal_gestor, nunca no DEFAULT_AUTHENTICATION_CLASSES global (que
    continua resolvendo para SupportUser via get_user_model()).
    """

    def get_user(self, validated_token):
        if validated_token.get('scope') != CLINIC_USER_TOKEN_SCOPE:
            raise AuthenticationFailed('token_wrong_scope', code='token_not_valid')

        user_id = validated_token.get('user_id')
        if user_id is None:
            raise AuthenticationFailed('token_missing_user_id', code='token_not_valid')

        try:
            clinic_user = ClinicUser.objects.select_related('clinic').get(id=user_id)
        except (ClinicUser.DoesNotExist, ValueError, TypeError):
            raise AuthenticationFailed('clinic_user_not_found', code='user_not_found')

        if not clinic_user.is_active:
            raise AuthenticationFailed('clinic_user_inactive', code='user_inactive')

        return clinic_user


# Nome do cookie httpOnly que carrega o access token nas páginas server-rendered
# do portal_gestor (Django Templates + HTMX, TASK-046). Mantido aqui (não em
# portal_gestor) porque é parte do contrato de autenticação, junto com
# CLINIC_USER_TOKEN_SCOPE.
PORTAL_ACCESS_COOKIE = 'portal_access_token'


class ClinicCookieJWTAuthentication(ClinicJWTAuthentication):
    """
    Variante de ClinicJWTAuthentication que lê o access token de um cookie
    httpOnly em vez do header `Authorization: Bearer` — usada nas views HTML
    server-rendered do portal_gestor (HTMX envia cookies automaticamente em
    cada request, sem JS gerenciando token).

    Reaproveita toda a validação de escopo/usuário/ativo de ClinicJWTAuthentication
    (get_user) — só muda de onde o raw token é lido.
    """

    def authenticate(self, request):
        raw_token = request.COOKIES.get(PORTAL_ACCESS_COOKIE)
        if raw_token is None:
            return None  # sem cookie: não autenticado, mas não é erro — deixa a permission_class decidir

        try:
            validated_token = self.get_validated_token(raw_token)
            return self.get_user(validated_token), validated_token
        except (AuthenticationFailed, TokenError):
            # Cookie presente mas inválido/expirado: trata como "sem credencial de
            # cookie" (None) em vez de propagar. Isso é o que já fazíamos de fato em
            # ClinicPortalAuthMiddleware (que envolve authenticate() num try/except
            # equivalente) — aqui formaliza o mesmo comportamento dentro da própria
            # classe, o que importa quando esta authenticator roda dentro de uma
            # cadeia authentication_classes=[Cookie, Bearer] (TASK-048/049,
            # DashboardSummaryView): sem isso, um cookie expirado faria a exceção
            # propagar e abortar a request ANTES do ClinicJWTAuthentication (bearer)
            # sequer ser tentado — mesmo que o header Authorization trouxesse um
            # token bearer válido. Com o catch, a cadeia cai corretamente para o
            # próximo authenticator.
            return None
