from django.contrib import admin
from syncro_backoffice.base_admin import BaseAdmin
from .models import Clinic


@admin.register(Clinic)
class ClinicAdmin(BaseAdmin):
    list_display = ('name', 'plan', 'status', 'license_key', 'created_at')
    list_filter = ('plan', 'status')
    search_fields = ('name', 'slug', 'contact_email')
    readonly_fields = ('id', 'license_key', 'created_at', 'updated_at')
    actions = ['suspend_clinics', 'activate_clinics']

    @admin.action(description='Suspender selecionadas')
    def suspend_clinics(self, request, queryset):
        queryset.update(status=Clinic.ClinicStatus.SUSPENDED if hasattr(Clinic, 'ClinicStatus') else 'suspended')

    @admin.action(description='Ativar selecionadas')
    def activate_clinics(self, request, queryset):
        queryset.update(status='active')
