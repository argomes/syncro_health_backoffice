from django.contrib import admin
from django.http import HttpResponse
from django.utils.html import format_html

from syncro_backoffice.base_admin import BaseAdmin, TenantScopedAdminMixin
from .models import TISSOperatorConfig, TISSLote, TISSGuia, TISSGlosa, TISSElegibilidadeConsulta
from .services import enviar_lote, TISSServiceError


@admin.register(TISSOperatorConfig)
class TISSOperatorConfigAdmin(TenantScopedAdminMixin, BaseAdmin):
    # login_encrypted/senha_encrypted NUNCA aparecem em list_display nem em
    # fields — só existem como TextField cifrado, não editável no admin
    # (edição é via os campos write-only `login`/`senha` do serializer da API).
    list_display = ('nome_operadora', 'registro_ans', 'clinic', 'ativo', 'created_at')
    list_filter = ('ativo',)
    search_fields = ('nome_operadora', 'registro_ans', 'clinic__name')
    readonly_fields = ('id', 'created_at', 'updated_at')
    exclude = ('login_encrypted', 'senha_encrypted')


@admin.register(TISSLote)
class TISSLoteAdmin(TenantScopedAdminMixin, BaseAdmin):
    list_display = ('numero_lote', 'clinic', 'competencia', 'status', 'protocolo', 'created_at')
    list_filter = ('status', 'competencia', 'clinic')
    search_fields = ('numero_lote', 'protocolo', 'clinic__name')
    readonly_fields = ('id', 'hash_epilogo', 'xml_enviado', 'xml_recebido', 'created_at', 'updated_at', 'enviado_at')
    actions = ['reenviar', 'baixar_xml_enviado', 'baixar_xml_recebido']

    @admin.action(description='Reenviar lote (mock/produção conforme TISS_SOAP_MOCK)')
    def reenviar(self, request, queryset):
        for lote in queryset:
            try:
                enviar_lote(lote)
            except TISSServiceError:
                # Erro já persistido em lote.erro_mensagem por enviar_lote — não
                # propaga aqui para não abortar o restante da seleção em massa.
                continue

    @admin.action(description='Baixar XML enviado (.xml)')
    def baixar_xml_enviado(self, request, queryset):
        lote = queryset.first()
        if not lote or not lote.xml_enviado:
            return None
        response = HttpResponse(lote.xml_enviado, content_type='application/xml')
        response['Content-Disposition'] = f'attachment; filename="lote_{lote.numero_lote}_enviado.xml"'
        return response

    @admin.action(description='Baixar XML recebido (.xml)')
    def baixar_xml_recebido(self, request, queryset):
        lote = queryset.first()
        if not lote or not lote.xml_recebido:
            return None
        response = HttpResponse(lote.xml_recebido, content_type='application/xml')
        response['Content-Disposition'] = f'attachment; filename="lote_{lote.numero_lote}_recebido.xml"'
        return response


@admin.register(TISSGuia)
class TISSGuiaAdmin(TenantScopedAdminMixin, BaseAdmin):
    list_display = ('numero', 'clinic', 'competencia', 'status_colorido', 'valor', 'created_at')
    list_filter = ('status', 'competencia', 'clinic')
    search_fields = ('numero', 'appointment_id', 'clinic__name')
    readonly_fields = ('id', 'created_at', 'updated_at')

    @admin.display(description='Status')
    def status_colorido(self, obj):
        cores = {
            'aceita': 'green',
            'glosada': 'red',
            'parcial': 'orange',
            'enviada': '#888',
            'nao_enviada': '#444',
        }
        cor = cores.get(obj.status, '#444')
        return format_html('<span style="color: {}; font-weight: bold;">{}</span>', cor, obj.get_status_display())


@admin.register(TISSGlosa)
class TISSGlosaAdmin(TenantScopedAdminMixin, BaseAdmin):
    # TISSGlosa não tem FK direta a Clinic (só via guia) — ver docstring do
    # model, isolamento propositalmente indireto.
    clinic_lookup = 'guia__clinic'
    list_display = ('codigo', 'guia', 'valor_glosado', 'recurso_enviado', 'created_at')
    list_filter = ('codigo', 'recurso_enviado')
    search_fields = ('codigo', 'guia__numero')
    readonly_fields = ('id', 'created_at')


@admin.register(TISSElegibilidadeConsulta)
class TISSElegibilidadeConsultaAdmin(TenantScopedAdminMixin, BaseAdmin):
    # Somente leitura — cada linha é um registro de auditoria imutável
    # (BACFF-013), não deve ser editado depois de criado pelo fluxo real.
    list_display = ('numero_carteira', 'clinic', 'origem', 'elegivel', 'created_at')
    list_filter = ('origem', 'elegivel', 'clinic')
    search_fields = ('numero_carteira', 'beneficiario_nome', 'numero_guia_operadora', 'clinic__name')
    readonly_fields = [f.name for f in TISSElegibilidadeConsulta._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
