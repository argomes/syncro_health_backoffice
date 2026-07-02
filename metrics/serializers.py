from rest_framework import serializers
from .models import SystemHeartbeat, SystemLog, LogLevel

class SystemHeartbeatSerializer(serializers.ModelSerializer):
    class Meta:
        model = SystemHeartbeat
        fields = [
            'id',
            'clinic',
            'gateway_version',
            'os_info',
            'db_size_mb',
            'pending_sync',
            'sync_connected',
            'last_seen',
        ]
        read_only_fields = ['id', 'last_seen']


class SystemLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = SystemLog
        fields = [
            'id',
            'clinic',
            'level',
            'message',
            'context',
            'occurred_at',
        ]
        read_only_fields = ['id', 'occurred_at']