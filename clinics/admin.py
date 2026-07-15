from django.contrib import admin
from syncro_backoffice.base_admin import BaseAdmin
from .models import Clinic


@admin.register(Clinic)
class ClinicAdmin(BaseAdmin):
    # TASK-056: license_key, public_key_pem e db_password_encrypted são
    # segredos de credenciamento/criptografia da clínica — nunca expostos a
    # grupos não-superuser (ex.: Analista Operacional), mesmo tendo
    # view_clinic. Ver get_list_display/get_fields abaixo.
    SENSITIVE_FIELDS = ('license_key', 'public_key_pem', 'db_password_encrypted')

    list_display = ('name', 'plan', 'status', 'license_key', 'created_at')
    list_filter = ('plan', 'status')
    search_fields = ('name', 'slug', 'contact_email')
    readonly_fields = ('id', 'license_key', 'created_at', 'updated_at')
    actions = ['suspend_clinics', 'activate_clinics']

    def get_list_display(self, request):
        list_display = super().get_list_display(request)
        if request.user.is_superuser:
            return list_display
        return tuple(f for f in list_display if f not in self.SENSITIVE_FIELDS)

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        if request.user.is_superuser:
            return fields
        return [f for f in fields if f not in self.SENSITIVE_FIELDS]

    def get_readonly_fields(self, request, obj=None):
        readonly = super().get_readonly_fields(request, obj)
        if request.user.is_superuser:
            return readonly
        return tuple(f for f in readonly if f not in self.SENSITIVE_FIELDS)

    @admin.action(description='Suspender selecionadas')
    def suspend_clinics(self, request, queryset):
        queryset.update(status=Clinic.ClinicStatus.SUSPENDED if hasattr(Clinic, 'ClinicStatus') else 'suspended')

    @admin.action(description='Ativar selecionadas')
    def activate_clinics(self, request, queryset):
        queryset.update(status='active')
