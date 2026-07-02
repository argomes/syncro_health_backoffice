from rest_framework import serializers
from .models import Clinic


class ClinicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Clinic
        fields = [
            'id', 'name', 'slug', 'license_key',
            'plan', 'status', 'contact_email', 'contact_phone',
            'provisioning_status',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'license_key', 'provisioning_status', 'created_at', 'updated_at']
        # public_key_pem, db_name, db_user, db_password_encrypted nunca expostos neste serializer
