from django.contrib import admin

from syncro_backoffice.base_admin import BaseAdmin, TenantScopedAdminMixin

from .models import Invoice, Plan


@admin.register(Plan)
class PlanAdmin(BaseAdmin):
    # Plan é catálogo global (não pertence a nenhuma clínica), então não usa
    # TenantScopedAdminMixin — todo SupportUser com view_plan enxerga a
    # mesma lista de planos.
    list_display = ('name', 'price', 'max_users', 'max_professionals', 'active')
    list_filter = ('active',)
    search_fields = ('name',)


@admin.register(Invoice)
class InvoiceAdmin(TenantScopedAdminMixin, BaseAdmin):
    # Fatura tem FK direta para Clinic — precisa do mesmo isolamento por
    # ClinicAccess que o resto dos models por-clínica (TenantScopedAdminMixin
    # default já usa clinic_lookup='clinic').
    list_display = ('clinic', 'competencia', 'amount', 'status', 'due_date', 'paid_at')
    list_filter = ('status',)
    search_fields = ('clinic__name', 'competencia')
    autocomplete_fields = ('clinic',)
    readonly_fields = ('id', 'created_at', 'updated_at')
