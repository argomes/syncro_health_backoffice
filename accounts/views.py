from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from clinics.models import Clinic
from .models import ClinicAccess, SupportUser
from .serializers import (
    ClinicAccessSerializer,
    ClinicAccessCreateSerializer,
    UserClinicAccessSerializer,
    SupportUserSerializer,
)


class ClinicAccessViewSet(viewsets.ModelViewSet):
    """
    Gerencia acesso de admins/support users a clínicas.

    Endpoints:
    - POST /api/accounts/clinic-access/ — Conceder acesso
    - GET /api/accounts/clinic-access/ — Listar acessos (filtrável)
    - PATCH /api/accounts/clinic-access/:id/ — Atualizar role
    - DELETE /api/accounts/clinic-access/:id/ — Revogar acesso (soft-delete)
    - GET /api/accounts/clinic-access/by-clinic/:clinic_id/ — Ver admins de uma clínica
    - GET /api/accounts/clinic-access/by-user/:user_id/ — Ver clínicas de um user
    - POST /api/accounts/clinic-access/:id/revoke/ — Revogar acesso explicitamente
    """

    queryset = ClinicAccess.objects.select_related('support_user', 'clinic', 'granted_by')
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['support_user__username', 'clinic__name']
    ordering_fields = ['granted_at', 'role', 'support_user__username']
    ordering = ['-granted_at']
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_serializer_class(self):
        if self.action in ['create', 'partial_update']:
            return ClinicAccessCreateSerializer
        return ClinicAccessSerializer

    def get_queryset(self):
        """Filtra acessos — admins veem todos, support/billing veem apenas seus."""
        qs = super().get_queryset()
        user = self.request.user

        if user.role == SupportUser.Role.ADMIN:
            return qs
        else:
            return qs.filter(support_user=user)

    # Hierarquia de roles de acesso à clínica (maior índice = maior privilégio)
    _ACCESS_ROLE_RANK = {
        ClinicAccess.AccessRole.VIEWER: 0,
        ClinicAccess.AccessRole.ADMIN: 1,
        ClinicAccess.AccessRole.OWNER: 2,
    }

    @classmethod
    def _get_role_rank(cls, role):
        """
        Retorna o rank do role na hierarquia de acesso. BACFF-002: um role
        desconhecido/inexistente NÃO pode cair silenciosamente em rank 0
        (viewer) — isso mascararia erro de dados ou tentativa de contornar a
        checagem de escalação de privilégio com um valor fora do enum.
        """
        if role not in cls._ACCESS_ROLE_RANK:
            raise PermissionDenied('invalid_role')
        return cls._ACCESS_ROLE_RANK[role]

    def perform_create(self, serializer):
        """Cria acesso e registra quem concedeu.

        Restrições de segurança:
        - Usuários não-admin só podem conceder roles iguais ou menores ao seu próprio
          role na clínica alvo.
        - Usuários não-admin só podem conceder acesso a clínicas às quais eles mesmos
          já têm acesso ativo.
        """
        user = self.request.user

        if user.role != SupportUser.Role.ADMIN:
            clinic = serializer.validated_data.get('clinic')
            requested_role = serializer.validated_data.get('role', ClinicAccess.AccessRole.VIEWER)

            # Verificar se o criador tem acesso ativo à clínica alvo
            creator_access = ClinicAccess.objects.filter(
                support_user=user,
                clinic=clinic,
                revoked_at__isnull=True,
            ).first()

            if creator_access is None:
                raise PermissionDenied('no_access_to_clinic')

            # Impedir escalação de privilégio: não pode conceder role maior que o próprio
            creator_rank = self._get_role_rank(creator_access.role)
            requested_rank = self._get_role_rank(requested_role)
            if requested_rank > creator_rank:
                raise PermissionDenied('role_escalation_denied')

        serializer.save(granted_by=user)

    @action(detail=True, methods=['post'])
    def revoke(self, request, pk=None):
        """
        POST /api/accounts/clinic-access/:id/revoke/
        Marca acesso como revogado (soft-delete).
        """
        access = self.get_object()

        if access.revoked_at is not None:
            return Response(
                {'error': 'already_revoked'},
                status=status.HTTP_400_BAD_REQUEST
            )

        access.revoked_at = timezone.now()
        access.save(update_fields=['revoked_at'])

        return Response(
            ClinicAccessSerializer(access).data,
            status=status.HTTP_200_OK
        )

    @action(detail=False, methods=['get'], url_path='by-clinic/(?P<clinic_id>[0-9a-f-]+)')
    def by_clinic(self, request, clinic_id=None):
        """
        GET /api/accounts/clinic-access/by-clinic/{clinic_id}/
        Lista todos os admins/support users que têm acesso a uma clínica.
        """
        clinic = get_object_or_404(Clinic, id=clinic_id)

        user = request.user
        if user.role != SupportUser.Role.ADMIN:
            has_access = ClinicAccess.objects.filter(
                support_user=user,
                clinic=clinic,
                revoked_at__isnull=True,
            ).exists()
            if not has_access:
                return Response({'error': 'forbidden'}, status=status.HTTP_403_FORBIDDEN)

        accesses = ClinicAccess.objects.filter(
            clinic=clinic,
            revoked_at__isnull=True
        ).select_related('support_user', 'granted_by')

        serializer = ClinicAccessSerializer(accesses, many=True)
        return Response({
            'clinic': clinic.name,
            'admins': serializer.data,
            'total': accesses.count(),
        })

    @action(detail=False, methods=['get'], url_path=r'by-user/(?P<user_id>\d+)')
    def by_user(self, request, user_id=None):
        """
        GET /api/accounts/clinic-access/by-user/{user_id}/
        Lista todas as clínicas que um user pode acessar.
        """
        user = request.user
        if user.role != SupportUser.Role.ADMIN and str(user.id) != str(user_id):
            return Response({'error': 'forbidden'}, status=status.HTTP_403_FORBIDDEN)

        support_user = get_object_or_404(SupportUser, id=user_id)

        accesses = ClinicAccess.objects.filter(
            support_user=support_user,
            revoked_at__isnull=True
        ).select_related('clinic')

        serializer = ClinicAccessSerializer(accesses, many=True)
        return Response({
            'user': support_user.username,
            'clinics': serializer.data,
            'total': accesses.count(),
        })

    @action(detail=False, methods=['get'], url_path='my-clinics')
    def my_clinics(self, request):
        """
        GET /api/accounts/clinic-access/my-clinics/
        Retorna as clínicas que o usuário autenticado pode acessar.
        """
        user = request.user
        accesses = ClinicAccess.objects.filter(
            support_user=user,
            revoked_at__isnull=True
        ).select_related('clinic')

        serializer = ClinicAccessSerializer(accesses, many=True)
        return Response({
            'user': user.username,
            'clinics': serializer.data,
            'total': accesses.count(),
        })


@api_view(['POST'])
@permission_classes([AllowAny])
def logout(request):
    """
    POST /api/auth/logout/
    BACFF-004: com ACCESS_TOKEN_LIFETIME reduzido mas ainda existente (2h), um
    refresh token comprometido continuaria válido por até 7 dias (
    REFRESH_TOKEN_LIFETIME) sem forma de revogação. Recebe o refresh token do
    SupportUser e o adiciona à blacklist (rest_framework_simplejwt.token_blacklist)
    — usá-lo novamente (inclusive para obter um novo access token) falha com 401.
    """
    refresh_token = request.data.get('refresh')
    if not refresh_token:
        return Response({'error': 'missing_refresh_token'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        token = RefreshToken(refresh_token)
        token.blacklist()
    except TokenError:
        return Response({'error': 'invalid_refresh_token'}, status=status.HTTP_401_UNAUTHORIZED)

    return Response(status=status.HTTP_205_RESET_CONTENT)


class ClinicFilterPermission:
    """
    Permissão global para filtrar clínicas que um user pode acessar.
    Aplicar em QuerySets com: queryset.filter_by_user_access(request.user)
    """

    @staticmethod
    def filter_clinics_for_user(user):
        """Retorna QuerySet de clínicas que user pode acessar."""
        if user.role == SupportUser.Role.ADMIN:
            return Clinic.objects.all()

        return Clinic.objects.filter(
            admin_accesses__support_user=user,
            admin_accesses__revoked_at__isnull=True
        ).distinct()
