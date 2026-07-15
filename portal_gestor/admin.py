from syncro_backoffice.base_admin import BaseAdmin, TenantScopedAdminMixin
from django.contrib import admin

from .models import ClinicUserNoticeDismissal, ProductNotice, ReportSession


@admin.register(ReportSession)
class ReportSessionAdmin(TenantScopedAdminMixin, BaseAdmin):
    """
    Somente leitura pelo suporte Syncro — a TemporaryKey nunca fica no banco,
    então não há nada sensível a proteger aqui além de metadados de auditoria.

    Ainda assim, o escopo de entidades/período de uma sessão de relatório
    (`entities_scope`, `date_from`/`date_to`) é metadado operacional de uma
    clínica específica: sem isolamento por ClinicAccess, um não-ADMIN
    enxergaria essas sessões de TODAS as clínicas no /admin/.
    """
    clinic_lookup = 'clinic'

    list_display = ('session_id', 'clinic', 'status', 'date_from', 'date_to', 'expires_at', 'created_at')
    list_filter = ('status', 'clinic')
    search_fields = ('session_id', 'clinic__name')
    readonly_fields = (
        'session_id', 'clinic', 'created_by', 'entities_scope', 'date_from', 'date_to',
        'status', 'expires_at', 'delivered_at', 'created_at', 'updated_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ProductNotice)
class ProductNoticeAdmin(BaseAdmin):
    """
    Suporte Syncro cria/edita/desativa avisos contextuais aqui, sem deploy
    (TASK-051). `active` é um kill-switch manual independente da vigência por
    data — útil para desligar um aviso na hora sem editar starts_at/ends_at.
    """
    list_display = ('title', 'active', 'starts_at', 'ends_at', 'is_current_display')
    list_filter = ('active',)
    search_fields = ('title', 'body')

    def is_current_display(self, obj):
        return obj.is_current()
    is_current_display.short_description = 'Vigente agora'
    is_current_display.boolean = True


@admin.register(ClinicUserNoticeDismissal)
class ClinicUserNoticeDismissalAdmin(TenantScopedAdminMixin, BaseAdmin):
    """
    Somente leitura — só para auditoria de quem já dispensou o quê.

    `ClinicUserNoticeDismissal` não tem FK direta a Clinic (só via
    `clinic_user`), mesmo padrão de FK indireta do TISSGlosaAdmin — sem
    isolamento, um não-ADMIN veria dispensas de aviso de usuários de
    qualquer clínica.
    """
    clinic_lookup = 'clinic_user__clinic'
    list_display = ('clinic_user', 'notice', 'dismissed_at')
    list_filter = ('notice',)
    search_fields = ('clinic_user__email', 'notice__title')
    readonly_fields = ('clinic_user', 'notice', 'dismissed_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
