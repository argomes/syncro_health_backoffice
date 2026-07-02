from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.db import models
from syncro_backoffice.base_admin import BaseAdmin
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from unfold.widgets import UnfoldAdminTextareaWidget, UnfoldAdminTextInputWidget
from .models import SupportUser, ClinicAccess


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
