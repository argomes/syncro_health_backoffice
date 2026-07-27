from rest_framework import serializers

from clinics.models import Clinic
from support.models import Ticket, TicketMessage

from .models import SUPPORTED_RESYNC_ENTITIES, ReportSession


class ReportSessionCreateSerializer(serializers.Serializer):
    date_from = serializers.DateTimeField()
    date_to = serializers.DateTimeField()
    entities = serializers.ListField(
        child=serializers.ChoiceField(choices=SUPPORTED_RESYNC_ENTITIES),
        required=False,
        default=list,
    )

    def validate(self, attrs):
        if attrs['date_from'] >= attrs['date_to']:
            raise serializers.ValidationError('date_from deve ser anterior a date_to.')
        if not attrs.get('entities'):
            attrs['entities'] = list(SUPPORTED_RESYNC_ENTITIES)
        return attrs


class ReportSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportSession
        fields = [
            'session_id', 'status', 'entities_scope',
            'date_from', 'date_to', 'expires_at', 'delivered_at', 'created_at',
        ]
        read_only_fields = fields


class PortalTicketListSerializer(serializers.ModelSerializer):
    """
    BACFF-AVULSA-09 — listagem "Chamados de Suporte" no portal_gestor. Nunca
    inclui `description` bruta (pode conter PHI citada pelo usuário no app
    desktop, regra 4.1 do CLAUDE.md) — só `category`, extraída via
    `Ticket.category` (parsing do cabeçalho estruturado, sem duplicar lógica).
    """
    category = serializers.CharField(read_only=True)

    class Meta:
        model = Ticket
        fields = ['id', 'title', 'category', 'status', 'priority', 'created_at', 'resolved_at']
        read_only_fields = fields


class PortalTicketMessageSerializer(serializers.ModelSerializer):
    author_username = serializers.CharField(source='author.username', read_only=True, default=None)

    class Meta:
        model = TicketMessage
        fields = ['id', 'author_username', 'message', 'created_at']
        read_only_fields = fields


class PortalTicketDetailSerializer(serializers.ModelSerializer):
    """
    Detalhe do ticket (consulta apenas — responder pelo portal é fora de
    escopo desta task). Inclui `description` (o próprio admin da clínica que
    a escreveu/reportou, então não há vazamento cross-tenant nem exposição a
    quem não deveria ver) e as mensagens já sincronizadas via webhook Zoho
    (BACFF-AVULSA-10).
    """
    category = serializers.CharField(read_only=True)
    messages = PortalTicketMessageSerializer(many=True, read_only=True)

    class Meta:
        model = Ticket
        fields = [
            'id', 'title', 'description', 'category', 'status', 'priority',
            'created_at', 'updated_at', 'resolved_at', 'messages',
        ]
        read_only_fields = fields


class SupportSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = Clinic
        fields = ['support_ticket_restricted_to_admin']
