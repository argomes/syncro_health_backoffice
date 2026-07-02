from rest_framework import serializers
from .models import Plan, Invoice


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = ['id', 'name', 'price', 'max_users', 'max_professionals', 'active']
        read_only_fields = ['id']


class InvoiceSerializer(serializers.ModelSerializer):
    clinic_name = serializers.CharField(source='clinic.name', read_only=True)

    class Meta:
        model = Invoice
        fields = [
            'id', 'clinic', 'clinic_name', 'competencia',
            'amount', 'status', 'due_date', 'paid_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'clinic_name', 'paid_at', 'created_at', 'updated_at']


class InvoiceUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = ['status', 'paid_at']
