from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import RefreshToken

from clinics.models import ClinicStatus

from .authentication import CLINIC_USER_TOKEN_SCOPE
from .models import ClinicUser


class ClinicTokenObtainPairSerializer(serializers.Serializer):
    """
    Login de ClinicUser (email/senha) para o portal_gestor.

    Não reaproveita o TokenObtainPairSerializer padrão porque este autentica
    contra get_user_model() (SupportUser) — ClinicUser é um model separado
    (ver accounts/models.py). O token emitido carrega um claim "scope" que
    accounts.authentication.ClinicJWTAuthentication exige para aceitar o token.
    """

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    default_error_messages = {
        'invalid_credentials': 'E-mail ou senha inválidos.',
        'clinic_inactive': 'Clínica inativa — contate o suporte.',
        'ldap_not_implemented': 'Login via LDAP ainda não está disponível para esta clínica.',
    }

    def validate(self, attrs):
        email = attrs['email'].strip().lower()
        password = attrs['password']

        try:
            clinic_user = ClinicUser.objects.select_related('clinic').get(email__iexact=email)
        except ClinicUser.DoesNotExist:
            self.fail('invalid_credentials')
        except ClinicUser.MultipleObjectsReturned:
            # Defesa em profundidade: ClinicUser.save() normaliza email para
            # minúsculas, então isso não deveria acontecer para registros novos,
            # mas dados legados/importados em lote poderiam ter variantes de case
            # que colidem sob __iexact. Tratar como credenciais inválidas em vez
            # de deixar vazar um 500.
            self.fail('invalid_credentials')

        if clinic_user.auth_provider != ClinicUser.AuthProvider.LOCAL:
            # LDAP é add-on pago (TASK-042a) — sem fallback silencioso para senha local.
            self.fail('ldap_not_implemented')

        if not clinic_user.is_active or not clinic_user.check_password(password):
            self.fail('invalid_credentials')

        if clinic_user.clinic.status != ClinicStatus.ACTIVE:
            self.fail('clinic_inactive')

        refresh = RefreshToken()
        refresh['scope'] = CLINIC_USER_TOKEN_SCOPE
        refresh['user_id'] = str(clinic_user.id)
        refresh['clinic_id'] = str(clinic_user.clinic_id)

        clinic_user.last_login = timezone.now()
        clinic_user.save(update_fields=['last_login'])

        return {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }


class ClinicTokenRefreshSerializer(serializers.Serializer):
    """
    Refresh dedicado para tokens de ClinicUser.

    Não reaproveita o TokenRefreshSerializer padrão: essa classe do simplejwt
    (rest_framework_simplejwt/serializers.py) sempre resolve o user_id do
    claim contra get_user_model() (SupportUser) antes de emitir um novo access
    token — um refresh token de ClinicUser (user_id é UUID) quebraria essa
    checagem com um erro 500 (ValueError: "Field 'id' expected a number").
    Ver accounts/tests_clinic_user.py::test_refresh_token_preserves_clinic_scope.
    """

    refresh = serializers.CharField()

    def validate(self, attrs):
        try:
            refresh = RefreshToken(attrs['refresh'])
        except TokenError as exc:
            raise serializers.ValidationError(str(exc), code='token_not_valid')

        if refresh.payload.get('scope') != CLINIC_USER_TOKEN_SCOPE:
            raise serializers.ValidationError('token_wrong_scope', code='token_not_valid')

        user_id = refresh.payload.get('user_id')
        try:
            clinic_user = ClinicUser.objects.select_related('clinic').get(id=user_id)
        except (ClinicUser.DoesNotExist, ValueError, TypeError):
            raise serializers.ValidationError('clinic_user_not_found', code='token_not_valid')

        if not clinic_user.is_active or clinic_user.clinic.status != ClinicStatus.ACTIVE:
            raise serializers.ValidationError('clinic_user_inactive', code='token_not_valid')

        data = {'access': str(refresh.access_token)}

        if jwt_settings.ROTATE_REFRESH_TOKENS:
            refresh.set_jti()
            refresh.set_exp()
            refresh.set_iat()
            data['refresh'] = str(refresh)

        return data
