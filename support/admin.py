from django.contrib import admin
from django.utils.html import format_html
from syncro_backoffice.base_admin import BaseAdmin, BaseTabularInline, TenantScopedAdminMixin
from .models import Ticket, TicketMessage


class TicketMessageInline(BaseTabularInline):
    """Inline para visualizar mensagens dentro de um ticket"""
    model = TicketMessage
    extra = 1
    readonly_fields = ['author', 'created_at']
    fields = ['author', 'message', 'created_at']


@admin.register(Ticket)
class TicketAdmin(TenantScopedAdminMixin, BaseAdmin):
    """
    Admin para gerenciar tickets de suporte.

    Tickets carregam descrição/contexto de suporte de uma clínica
    específica — sem isolamento, um SupportUser não-admin com a permissão
    já concedida ao grupo "Analista Operacional" (TASK-056) veria tickets de
    TODAS as clínicas no /admin/.
    """
    clinic_lookup = 'clinic'

    list_display = [
        'title_truncated',
        'clinic',
        'status_badge',
        'priority_badge',
        'assigned_to',
        'created_at',
        'message_count',
    ]
    list_filter = ['status', 'priority', 'created_at', 'clinic']
    search_fields = ['title', 'description', 'clinic__name']
    readonly_fields = ['created_at', 'updated_at', 'resolved_at', 'notion_page_id', 'zoho_ticket_id']
    inlines = [TicketMessageInline]

    fieldsets = (
        ('Informações Básicas', {
            'fields': ('title', 'description', 'clinic')
        }),
        ('Status & Prioridade', {
            'fields': ('status', 'priority', 'assigned_to')
        }),
        ('Integração', {
            'fields': ('zoho_ticket_id', 'notion_page_id'),
            'description': (
                'notion_page_id é legado (BACFF-AVULSA-07) — só referência '
                'histórica de tickets criados antes da migração para Zoho Desk.'
            ),
            'classes': ('collapse',)
        }),
        ('Auditoria', {
            'fields': ('created_by', 'created_at', 'updated_at', 'resolved_at'),
            'classes': ('collapse',)
        }),
    )

    ordering = ['-created_at']

    def title_truncated(self, obj):
        """Título truncado para exibição"""
        return obj.title[:50] + '...' if len(obj.title) > 50 else obj.title
    title_truncated.short_description = 'Título'

    def status_badge(self, obj):
        """Badge colorido para status"""
        colors = {
            'open': '#FFA500',        # Orange
            'in_progress': '#87CEEB', # Sky Blue
            'resolved': '#90EE90',    # Light Green
            'closed': '#808080',      # Gray
        }
        color = colors.get(obj.status, '#CCCCCC')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'

    def priority_badge(self, obj):
        """Badge colorido para prioridade"""
        colors = {
            'low': '#90EE90',      # Light Green
            'medium': '#FFD700',   # Gold
            'high': '#FF6347',     # Tomato
            'critical': '#DC143C', # Crimson
        }
        color = colors.get(obj.priority, '#CCCCCC')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.get_priority_display()
        )
    priority_badge.short_description = 'Prioridade'

    def message_count(self, obj):
        """Conta de mensagens no ticket"""
        count = obj.messages.count()
        return format_html(
            '<span style="background-color: #E8E8E8; padding: 2px 6px; border-radius: 3px;">{} msg</span>',
            count
        )
    message_count.short_description = 'Mensagens'

    actions = ['mark_as_in_progress', 'mark_as_resolved', 'mark_as_closed']

    def mark_as_in_progress(self, request, queryset):
        """Ação: marcar como em progresso"""
        updated = queryset.update(status='in_progress')
        self.message_user(request, f'{updated} tickets marcados como "Em Progresso"')
    mark_as_in_progress.short_description = '✓ Marcar como Em Progresso'

    def mark_as_resolved(self, request, queryset):
        """Ação: marcar como resolvido"""
        from django.utils import timezone
        updated = queryset.update(status='resolved', resolved_at=timezone.now())
        self.message_user(request, f'{updated} tickets marcados como "Resolvido"')
    mark_as_resolved.short_description = '✓ Marcar como Resolvido'

    def mark_as_closed(self, request, queryset):
        """Ação: marcar como fechado"""
        updated = queryset.update(status='closed')
        self.message_user(request, f'{updated} tickets marcados como "Fechado"')
    mark_as_closed.short_description = '✓ Marcar como Fechado'


@admin.register(TicketMessage)
class TicketMessageAdmin(TenantScopedAdminMixin, BaseAdmin):
    """
    Admin para gerenciar mensagens de tickets.

    TicketMessage não tem FK direta a Clinic (só via `ticket`) — mesmo
    padrão de FK indireta do TISSGlosaAdmin (`guia__clinic`).
    """
    clinic_lookup = 'ticket__clinic'

    list_display = ['ticket', 'author', 'message_preview', 'created_at']
    list_filter = ['ticket__status', 'created_at', 'author']
    search_fields = ['ticket__title', 'message', 'author__username']
    readonly_fields = ['created_at', 'ticket']

    fieldsets = (
        ('Mensagem', {
            'fields': ('ticket', 'author', 'message')
        }),
        ('Auditoria', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )

    ordering = ['-created_at']

    def message_preview(self, obj):
        """Preview da mensagem truncada"""
        return obj.message[:100] + '...' if len(obj.message) > 100 else obj.message
    message_preview.short_description = 'Mensagem'
