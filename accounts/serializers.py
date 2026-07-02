from rest_framework import serializers
from django.contrib.auth import get_user_model

from clinics.serializers import ClinicSerializer
from .models import ClinicAccess

User = get_user_model()


class SupportUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'role', 'is_active']
        read_only_fields = ['id']


class ClinicAccessSerializer(serializers.ModelSerializer):
    clinic_detail = ClinicSerializer(source='clinic', read_only=True)
    granted_by_detail = SupportUserSerializer(source='granted_by', read_only=True)
    is_active = serializers.SerializerMethodField()

    class Meta:
        model = ClinicAccess
        fields = [
            'id',
            'support_user',
            'clinic',
            'clinic_detail',
            'role',
            'granted_at',
            'granted_by',
            'granted_by_detail',
            'revoked_at',
            'is_active',
        ]
        read_only_fields = ['id', 'granted_at', 'revoked_at']

    def get_is_active(self, obj):
        return obj.is_active()


class ClinicAccessCreateSerializer(serializers.ModelSerializer):
    """Serializer para criar/atualizar acesso de clínica."""

    class Meta:
        model = ClinicAccess
        fields = ['support_user', 'clinic', 'role']

    def create(self, validated_data):
        # Adiciona quem concedeu acesso
        validated_data['granted_by'] = self.context['request'].user
        return super().create(validated_data)


class UserClinicAccessSerializer(serializers.Serializer):
    """Lista todas as clínicas que um user pode acessar."""
    user_id = serializers.IntegerField()
    username = serializers.CharField()
    clinics = ClinicAccessSerializer(source='clinic_accesses', many=True)
    total_clinics = serializers.SerializerMethodField()

    def get_total_clinics(self, obj):
        return obj.clinic_accesses.filter(revoked_at__isnull=True).count()
