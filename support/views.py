from django.utils import timezone
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Ticket, TicketMessage
from .serializers import TicketSerializer, TicketMessageSerializer, TicketCreateSerializer, TicketMessageCreateSerializer
from .permissions import IsAuthenticatedByLicenseKeyOrJWT
from .services import NotionService

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
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        """Marca ticket como resolvido"""
        ticket = self.get_object()
        ticket.status = 'resolved'
        ticket.resolved_at = timezone.now()
        ticket.save()
        return Response(TicketSerializer(ticket).data)


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

