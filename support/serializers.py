from rest_framework import serializers
from .models import Ticket, TicketMessage


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

    def create(self, validated_data):
        ticket_id = self.context['ticket_id']
        validated_data['ticket_id'] = ticket_id
        validated_data['author'] = self.context['request'].user
        return super().create(validated_data)
