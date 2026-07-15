from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from datetime import timedelta
from syncro_backoffice.base_admin import BaseAdmin

from .models import SystemHeartbeat, SystemLog


@admin.register(SystemHeartbeat)
class SystemHeartbeatAdmin(BaseAdmin):
    list_display = ('clinic_name', 'gateway_version', 'db_size_mb', 'pending_sync', 'sync_status', 'last_seen_status')
    list_filter = ('sync_connected',)
    search_fields = ('clinic__name',)
    readonly_fields = ('clinic', 'gateway_version', 'os_info', 'db_size_mb', 'pending_sync', 'sync_connected', 'last_seen', 'created_at')

    def clinic_name(self, obj):
        return obj.clinic.name
    clinic_name.short_description = 'Clínica'

    def sync_status(self, obj):
        if obj.sync_connected:
            return mark_safe('<span style="color:green">✔ Conectado</span>')
        return mark_safe('<span style="color:orange">✘ Offline</span>')
    sync_status.short_description = 'Sync'

    def last_seen_status(self, obj):
        cutoff = timezone.now() - timedelta(minutes=30)
        if obj.last_seen < cutoff:
            return format_html('<span style="color:red">{}</span>', obj.last_seen.strftime('%Y-%m-%d %H:%M'))
        return obj.last_seen.strftime('%Y-%m-%d %H:%M')
    last_seen_status.short_description = 'Último heartbeat'

    def has_add_permission(self, request):
        return False


class LastSeenFilter(admin.SimpleListFilter):
    title = 'Período'
    parameter_name = 'period'

    def lookups(self, request, model_admin):
        return [('24h', 'Últimas 24h'), ('7d', 'Últimos 7 dias')]

    def queryset(self, request, queryset):
        if self.value() == '24h':
            return queryset.filter(occurred_at__gte=timezone.now() - timedelta(hours=24))
        if self.value() == '7d':
            return queryset.filter(occurred_at__gte=timezone.now() - timedelta(days=7))
        return queryset


@admin.register(SystemLog)
class SystemLogAdmin(BaseAdmin):
    list_display = ('clinic_name', 'level_badge', 'message_short', 'occurred_at')
    list_filter = ('level', LastSeenFilter)
    search_fields = ('clinic__name', 'message')
    readonly_fields = ('clinic', 'level', 'message', 'context', 'occurred_at', 'created_at')

    def clinic_name(self, obj):
        return obj.clinic.name
    clinic_name.short_description = 'Clínica'

    def level_badge(self, obj):
        colors = {'error': 'red', 'warn': 'orange', 'info': 'steelblue'}
        color = colors.get(obj.level, 'gray')
        return format_html('<span style="color:{}; font-weight:bold">{}</span>', color, obj.level.upper())
    level_badge.short_description = 'Nível'

    def message_short(self, obj):
        return obj.message[:80]
    message_short.short_description = 'Mensagem'

    def has_add_permission(self, request):
        return False
