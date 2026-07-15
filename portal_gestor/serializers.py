from rest_framework import serializers

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
