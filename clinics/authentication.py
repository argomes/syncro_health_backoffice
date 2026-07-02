from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
import jwt
from clinics.models import Clinic, ClinicStatus
from .service_tokens import decode_service_token


class ServiceTokenAuthentication(BaseAuthentication):
    """
    Autenticação DRF baseada em Service Token (JWT assinado com RSA-256).
    Espera o header: Authorization: Bearer <service_token>
    Adiciona `request.clinic` com a instância da clínica e retorna (None, payload).
    """

    def authenticate(self, request):
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            return None

        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            return None

        token = parts[1]
        try:
            payload = decode_service_token(token)
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationFailed('Token expirado') from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationFailed('Token inválido') from exc

        clinic_id = payload.get('clinic_id')
        if not clinic_id:
            raise AuthenticationFailed('Token inválido: clinic_id ausente')

        try:
            # Apenas clínicas com licença/status ativo
            clinic = Clinic.objects.get(id=clinic_id)
        except Clinic.DoesNotExist as exc:
            raise AuthenticationFailed('Clínica não encontrada') from exc

        if clinic.status != ClinicStatus.ACTIVE:
            raise AuthenticationFailed(f'Acesso negado: clínica {clinic.status}')

        # Injeta a clínica no request
        request.clinic = clinic
        return (None, payload)

    def authenticate_header(self, request):
        return 'Bearer realm="api"'
