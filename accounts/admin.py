from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.db import models
from syncro_backoffice.base_admin import BaseAdmin
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from unfold.widgets import UnfoldAdminTextareaWidget, UnfoldAdminTextInputWidget
from .models import SupportUser, ClinicAccess, ClinicUser


@admin.register(SupportUser)
class SupportUserAdmin(UnfoldModelAdmin, UserAdmin):
    """
    Herança múltipla: UnfoldModelAdmin antes de UserAdmin para que os
    templates e widgets do Unfold tenham precedência sobre os do Django Admin.
    """
    list_display = ('username', 'email', 'role', 'is_active')
    list_filter = ('role', 'is_active')
    fieldsets = UserAdmin.fieldsets + (
        ('Syncro', {'fields': ('role',)}),
    )
    formfield_overrides = {
        models.CharField: {"widget": UnfoldAdminTextInputWidget},
        models.TextField: {"widget": UnfoldAdminTextareaWidget},
    }


@admin.register(ClinicAccess)
class ClinicAccessAdmin(BaseAdmin):
    list_display = ('support_user', 'clinic', 'role', 'is_active', 'granted_at')
    list_filter = ('role', 'granted_at', 'revoked_at')
    search_fields = ('support_user__username', 'clinic__name')
    readonly_fields = ('granted_at', 'granted_by', 'revoked_at')
    fieldsets = (
        ('Acesso', {
            'fields': ('support_user', 'clinic', 'role')
        }),
        ('Auditoria', {
            'fields': ('granted_by', 'granted_at', 'revoked_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(ClinicUser)
class ClinicUserAdmin(BaseAdmin):
    """
    Cadastro/gestão de usuários da clínica (portal_gestor) pelo suporte Syncro.
    A senha nunca é exibida — só pode ser redefinida (write-only), nunca lida.
    """
    list_display = ('email', 'name', 'clinic', 'auth_provider', 'is_active', 'last_login')
    list_filter = ('auth_provider', 'is_active', 'clinic')
    search_fields = ('email', 'name', 'clinic__name')
    readonly_fields = ('last_login', 'created_at', 'updated_at')
    exclude = ('password',)
