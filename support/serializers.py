from rest_framework import serializers
from rest_framework.exceptions import NotFound
from .models import Ticket, TicketMessage


def _ticket_accessible(request, ticket_id):
    """
    Mesma regra de isolamento usada em TicketMessageViewSet.get_queryset
    (BO-SEC-002): retorna o Ticket se o autor da requisição (Edge via
    license_key ou support user via JWT) tem acesso a ele, senão None.
    Reaproveitada aqui para blindar o create, que antes confiava cegamente
    no ticket_id da URL.
    """
    if hasattr(request, 'clinic') and request.clinic:
        return Ticket.objects.filter(id=ticket_id, clinic=request.clinic).first()

    user = request.user
    if user and hasattr(user, 'role') and user.role == 'admin':
        return Ticket.objects.filter(id=ticket_id).first()

    if user and user.is_authenticated:
        return Ticket.objects.filter(
            id=ticket_id,
            clinic__admin_accesses__support_user=user,
        ).distinct().first()

    return None


class TicketMessageSerializer(serializers.ModelSerializer):
    author_username = serializers.CharField(source='author.username', read_only=True)

    class Meta:
        model = TicketMessage
        fields = ['id', 'message', 'author', 'author_username', 'created_at']
        read_only_fields = ['id', 'author', 'created_at']


class TicketSerializer(serializers.ModelSerializer):
    messages = TicketMessageSerializer(many=True, read_only=True)
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    assigned_to_username = serializers.CharField(source='assigned_to.username', read_only=True, allow_null=True)
    clinic_name = serializers.CharField(source='clinic.name', read_only=True)

    class Meta:
        model = Ticket
        fields = [
            'id',
            'clinic',
            'clinic_name',
            'created_by',
            'created_by_username',
            'assigned_to',
            'assigned_to_username',
            'title',
            'description',
            'status',
            'priority',
            'notion_page_id',
            'zoho_ticket_id',
            'created_at',
            'updated_at',
            'resolved_at',
            'messages',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'resolved_at', 'messages']


class TicketCreateSerializer(serializers.ModelSerializer):
    """Serializer simplificado para criar tickets"""

    class Meta:
        model = Ticket
        fields = ['clinic', 'title', 'description', 'priority']

    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


class TicketMessageCreateSerializer(serializers.ModelSerializer):
    """Serializer para adicionar mensagens"""

    class Meta:
        model = TicketMessage
        fields = ['message']

    def validate(self, attrs):
        request = self.context['request']
        ticket_id = self.context['ticket_id']
        # BO-SEC-008: bloqueia POST de mensagem em ticket de outra clínica —
        # antes o create confiava cegamente no ticket_id da URL.
        if _ticket_accessible(request, ticket_id) is None:
            # 404 em vez de 403 — não confirma nem nega a existência do
            # ticket alheio, mesmo espírito do isolamento em get_queryset.
            raise NotFound('Ticket não encontrado.')
        return attrs

    def create(self, validated_data):
        ticket_id = self.context['ticket_id']
        validated_data['ticket_id'] = ticket_id
        validated_data['author'] = self.context['request'].user
        return super().create(validated_data)
