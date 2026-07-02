from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

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

    def perform_create(self, serializer):
        """Cria acesso e registra quem concedeu."""
        serializer.save(granted_by=self.request.user)

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
