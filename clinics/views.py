from django.utils import timezone
from django.core.cache import cache
from datetime import timedelta
import logging

from rest_framework import viewsets, filters, status
from rest_framework.decorators import action, api_view, permission_classes, authentication_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from syncro_backoffice.throttling import ServiceTokenRateThrottle, LicenseRateThrottle, DbAccessGrantRateThrottle

from .models import Clinic, ClinicStatus, ProvisioningStatus
from .serializers import ClinicSerializer
from .permissions import IsAuthenticatedByLicenseKey, IsAuthenticatedByAnyLicenseKey, IsServiceTokenAuthenticated
from .service_tokens import generate_service_token
from .authentication import ServiceTokenAuthentication

logger = logging.getLogger(__name__)

DB_ACCESS_GRANT_DEFAULT_TTL_SECONDS = 14400  # 4h
DB_ACCESS_GRANT_MAX_TTL_SECONDS = 14400  # 4h


class ClinicViewSet(viewsets.ModelViewSet):
    queryset = Clinic.objects.all()
    serializer_class = ClinicSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'slug', 'contact_email']
    ordering_fields = ['name', 'plan', 'status', 'created_at']
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    @action(
        detail=False,
        methods=['post'],
        url_path='register-key',
        permission_classes=[IsAuthenticatedByLicenseKey],
    )
    def register_key(self, request):
        """
        Recebe a chave pública RSA do app desktop.
        Autenticado por license_key — não requer JWT.
        Dispara o provisionamento do banco automaticamente via signal.
        """
        clinic: Clinic = request.clinic
        public_key_pem = request.data.get('public_key_pem', '').strip()

        if not public_key_pem:
            return Response({'error': 'public_key_pem_required'}, status=status.HTTP_400_BAD_REQUEST)

        if clinic.provisioning_status == ProvisioningStatus.PROVISIONED:
            return Response({'error': 'already_provisioned'}, status=status.HTTP_409_CONFLICT)

        clinic.public_key_pem = public_key_pem
        clinic.save(update_fields=['public_key_pem'])
        # Signal provision_on_key_received é disparado pelo save acima

        clinic.refresh_from_db()
        return Response({'provisioning_status': clinic.provisioning_status})

    @action(
        detail=False,
        methods=['get'],
        url_path='credentials',
        permission_classes=[IsAuthenticatedByLicenseKey],
    )
    def credentials(self, request):
        """
        Retorna as credenciais do banco criptografadas com a chave pública da clínica.
        Apenas o app desktop consegue descriptografar com sua chave privada.
        """
        clinic: Clinic = request.clinic

        if clinic.provisioning_status != ProvisioningStatus.PROVISIONED:
            return Response(
                {'error': 'not_provisioned', 'status': clinic.provisioning_status},
                status=status.HTTP_424_FAILED_DEPENDENCY,
            )

        return Response({
            'db_name': clinic.db_name,
            'db_user': clinic.db_user,
            'db_password_encrypted': clinic.db_password_encrypted,
        })

    @action(
        detail=False,
        methods=['post'],
        url_path='db-access-grant',
        permission_classes=[IsAuthenticatedByLicenseKey],
        throttle_classes=[DbAccessGrantRateThrottle],
    )
    def db_access_grant(self, request):
        """
        Recebe do gateway local uma senha temporária (em texto plano, já
        decriptada pela chave privada RSA da clínica) autorizando o
        Backoffice a conectar ao banco da clínica como o db_user escopado
        (não superuser), por um período limitado (BACFF-AVULSA-06).

        A credencial vive só em cache (Redis), nunca em modelo relacional —
        expira sozinha via TTL nativo do cache, sem job de limpeza.
        """
        clinic: Clinic = request.clinic

        if clinic.provisioning_status != ProvisioningStatus.PROVISIONED:
            return Response(
                {'error': 'not_provisioned', 'status': clinic.provisioning_status},
                status=status.HTTP_424_FAILED_DEPENDENCY,
            )

        db_password = str(request.data.get('db_password', '')).strip()
        if not db_password:
            return Response({'error': 'db_password_required'}, status=status.HTTP_400_BAD_REQUEST)

        ttl_seconds = request.data.get('ttl_seconds', DB_ACCESS_GRANT_DEFAULT_TTL_SECONDS)
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or ttl_seconds <= 0 \
                or ttl_seconds > DB_ACCESS_GRANT_MAX_TTL_SECONDS:
            return Response({'error': 'ttl_seconds_invalid'}, status=status.HTTP_400_BAD_REQUEST)

        granted_by = str(request.data.get('granted_by', '')).strip()
        now = timezone.now()
        expires_at = now + timedelta(seconds=ttl_seconds)

        cache.set(f"clinic_db_grant:{clinic.id}", {
            'password': db_password,
            'granted_by': granted_by,
            'granted_at': now.isoformat(),
            'expires_at': expires_at.isoformat(),
        }, timeout=ttl_seconds)

        logger.info(
            "db_access_grant concedido clinic_id=%s granted_by=%s ttl=%s",
            clinic.id, granted_by, ttl_seconds,
        )  # nunca logar a senha

        return Response({'granted': True, 'expires_at': expires_at.isoformat()})

    @action(
        detail=False,
        methods=['post'],
        url_path='validate-license',
        permission_classes=[IsAuthenticatedByAnyLicenseKey],
    )
    def validate_license(self, request):
        """
        Valida a licença do app desktop.
        Autenticado por license_key — não requer JWT.
        Retorna status da clínica e dados de provisionamento.
        """
        clinic: Clinic = request.clinic

        if clinic.status == ClinicStatus.SUSPENDED:
            return Response(
                {'error': 'license_suspended', 'status': 'suspended'},
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )

        if clinic.status == ClinicStatus.CANCELLED:
            return Response(
                {'error': 'license_cancelled', 'status': 'cancelled'},
                status=status.HTTP_403_FORBIDDEN,
            )

        return Response({
            'status': clinic.status,
            'plan': clinic.plan,
            'clinic_name': clinic.name,
            'cnpj': clinic.cnpj,
            'db_name': clinic.db_name,
            'provisioning_status': clinic.provisioning_status,
        })
        
    def get_queryset(self):
        """
        Filtra clínicas baseado no admin logado
        """
        user = self.request.user
        if user.role == 'admin':
            return Clinic.objects.all()

        # Support/billing veem apenas suas clínicas
        return Clinic.objects.filter(
            admin_accesses__support_user=user,
            admin_accesses__revoked_at__isnull=True
        ).distinct()

@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([ServiceTokenRateThrottle])
def issue_service_token(request):
    """
    POST /api/v1/auth/service-token/
    Recebe license_key e retorna um Service Token (JWT) assinado com RSA.
    """
    license_key = request.data.get('license_key') or request.headers.get('X-License-Key')
    if not license_key:
        return Response({'error': 'missing_license_key'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        clinic = Clinic.objects.get(license_key=license_key)
    except (Clinic.DoesNotExist, ValueError):
        return Response({'error': 'invalid_license'}, status=status.HTTP_401_UNAUTHORIZED)

    if clinic.status != ClinicStatus.ACTIVE:
        return Response({'error': f'clinic_{clinic.status}'}, status=status.HTTP_403_FORBIDDEN)

    if clinic.license_expires_at and timezone.now() > clinic.license_expires_at:
        return Response({'error': 'license_expired'}, status=status.HTTP_403_FORBIDDEN)

    token = generate_service_token(
        clinic_id=str(clinic.id),
        plan=clinic.plan,
        active_modules=clinic.active_modules,
        expires_in_hours=24
    )

    expires_at = timezone.now() + timezone.timedelta(hours=24)
    return Response({
        'service_token': token,
        'expires_at': int(expires_at.timestamp())
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@authentication_classes([ServiceTokenAuthentication])
@permission_classes([IsServiceTokenAuthenticated])
@throttle_classes([LicenseRateThrottle])
def get_license_info(request):
    """
    GET /api/v1/license
    Retorna informações da licença da clínica (autenticado por Service Token).
    """
    clinic = request.clinic
    if clinic.license_expires_at and timezone.now() > clinic.license_expires_at:
        return Response({'error': 'license_expired'}, status=status.HTTP_403_FORBIDDEN)

    return Response({
        'clinic_id': str(clinic.id),
        'clinic_name': clinic.name,
        'status': clinic.status,
        'plan': clinic.plan,
        'active_modules': clinic.active_modules,
        'license_expires_at': clinic.license_expires_at.isoformat() if clinic.license_expires_at else None
    }, status=status.HTTP_200_OK)

