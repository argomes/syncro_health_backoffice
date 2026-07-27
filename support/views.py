import logging

from django.utils import timezone
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from accounts.models import ClinicAccess, SupportUser
from clinics.permissions import IsAuthenticatedByLicenseKey
from syncro_backoffice.throttling import ErrorReportRateThrottle
from .models import Ticket, TicketMessage
from .serializers import (
    TicketSerializer,
    TicketMessageSerializer,
    TicketCreateSerializer,
    TicketMessageCreateSerializer,
    ErrorReportCreateSerializer,
)
from .permissions import IsAuthenticatedByLicenseKeyOrJWT

logger = logging.getLogger(__name__)
# NotionService não é mais usado aqui (nunca era, na verdade — import morto)
# nem no fluxo ativo de sync (BACFF-AVULSA-07: Zoho Desk substituiu o
# Notion). Sincronização acontece via signal em support/models.py.

class TicketViewSet(viewsets.ModelViewSet):
    """Gerencia suporte tickets"""
    permission_classes = [IsAuthenticatedByLicenseKeyOrJWT]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'clinic__name']
    ordering_fields = ['created_at', 'priority', 'status']
    ordering = ['-created_at']
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        # Se autenticado via license_key (Edge), retorna tickets da clínica
        if hasattr(self.request, 'clinic') and self.request.clinic:
            return Ticket.objects.filter(clinic=self.request.clinic)

        # Se autenticado via JWT (Admin)
        user = self.request.user
        if user and hasattr(user, 'role') and user.role == 'admin':
            return Ticket.objects.all()

        # Support user vê tickets da sua clínica
        if user and user.is_authenticated:
            return Ticket.objects.filter(clinic__admin_accesses__support_user=user).distinct()

        return Ticket.objects.none()

    def get_serializer_class(self):
        if self.action in ['create']:
            return TicketCreateSerializer
        return TicketSerializer

    def perform_create(self, serializer):
        request = self.request
        clinic = serializer.validated_data.get('clinic')

        # BO-SEC-009: `clinic` é gravável em TicketCreateSerializer — sem essa
        # checagem, qualquer usuário autenticado cria ticket em nome de
        # clínica arbitrária (mass assignment / IDOR).
        if hasattr(request, 'clinic') and request.clinic:
            # Edge Gateway (license_key) só cria ticket para a própria clínica.
            if clinic is not None and clinic.id != request.clinic.id:
                raise PermissionDenied('Sem acesso a esta clínica.')
            serializer.save(clinic=request.clinic, created_by=None)
            return

        user = request.user
        if user and hasattr(user, 'role') and user.role == SupportUser.Role.ADMIN:
            serializer.save(created_by=user)
            return

        if user and user.is_authenticated:
            has_access = clinic is not None and ClinicAccess.objects.filter(
                support_user=user,
                clinic=clinic,
                revoked_at__isnull=True,
            ).exists()
            if not has_access:
                raise PermissionDenied('Sem acesso a esta clínica.')
            serializer.save(created_by=user)
            return

        raise PermissionDenied('Sem acesso a esta clínica.')

    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        """Marca ticket como resolvido"""
        ticket = self.get_object()
        ticket.status = 'resolved'
        ticket.resolved_at = timezone.now()
        ticket.save()
        return Response(TicketSerializer(ticket).data)


@api_view(['POST'])
@permission_classes([IsAuthenticatedByLicenseKey])
@throttle_classes([ErrorReportRateThrottle])
def create_error_report(request):
    """
    POST /api/support/error-reports/  (EDGW-052)

    Recebido do gateway local (syncro_gateway), que repassa o
    `POST /error-reports` do app desktop autenticado por `X-License-Key` —
    mesmo padrão de `clinics/permissions.py::IsAuthenticatedByLicenseKey`
    usado em `clinics/views.py` (ex.: register_key, credentials).

    Cria um `Ticket` local vinculado à clínica via `Ticket.objects.create()`
    normal, o que já dispara o signal `sync_ticket_to_zoho`
    (BACFF-AVULSA-07) — não duplicamos essa lógica aqui. A sync com o Zoho
    é assíncrona (Celery); não bloqueamos a resposta esperando o resultado
    dela, então `zoho_ticket_id` pode voltar vazio se a task ainda não
    rodou ou falhou.

    Nunca logamos `description`/`contact_name`/`contact_email` do payload
    (pode conter PHI citada pelo usuário do desktop — regra 4.1 do
    CLAUDE.md) — só IDs/metadados.

    Nota (Security Engineer, 2026-07-27): este endpoint NÃO valida
    `clinic.support_ticket_restricted_to_admin` — essa checagem é
    responsabilidade do gateway (EDGW-052, lado syncro_gateway). Não
    assumir aqui que role/permissão já foi validada.
    """
    serializer = ErrorReportCreateSerializer(data=request.data, context={'clinic': request.clinic})
    serializer.is_valid(raise_exception=True)
    ticket = serializer.save()

    logger.info(
        "create_error_report: ticket %s criado para clinic_id=%s (priority=%s)",
        ticket.id, request.clinic.id, ticket.priority,
    )

    return Response(
        {
            'ticket_id': ticket.id,
            'zoho_ticket_id': ticket.zoho_ticket_id,
            'status': ticket.status,
        },
        status=status.HTTP_201_CREATED,
    )


class TicketMessageViewSet(viewsets.ModelViewSet):
    """Gerencia mensagens de ticket"""
    permission_classes = [IsAuthenticatedByLicenseKeyOrJWT]
    ordering = ['created_at']
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        ticket_id = self.kwargs.get('ticket_id')

        # Se autenticado via license_key (Edge), restringe às mensagens de
        # tickets da própria clínica.
        if hasattr(self.request, 'clinic') and self.request.clinic:
            return TicketMessage.objects.filter(
                ticket_id=ticket_id,
                ticket__clinic=self.request.clinic,
            )

        # Se autenticado via JWT (Admin), acesso irrestrito.
        user = self.request.user
        if user and hasattr(user, 'role') and user.role == 'admin':
            return TicketMessage.objects.filter(ticket_id=ticket_id)

        # Support user só vê mensagens de tickets de clínicas às quais tem acesso.
        if user and user.is_authenticated:
            return TicketMessage.objects.filter(
                ticket_id=ticket_id,
                ticket__clinic__admin_accesses__support_user=user,
            ).distinct()

        return TicketMessage.objects.none()

    def get_serializer_class(self):
        if self.action in ['create']:
            return TicketMessageCreateSerializer
        return TicketMessageSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['ticket_id'] = self.kwargs.get('ticket_id')
        return context

