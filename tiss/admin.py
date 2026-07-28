from django.contrib import admin
from django.http import HttpResponse
from django.utils.html import format_html

from syncro_backoffice.base_admin import BaseAdmin, TenantScopedAdminMixin
from . import providers
from .models import (
    TISSOperatorConfig, TISSLote, TISSGuia, TISSGlosa, TISSElegibilidadeConsulta,
    TUSSProcedureCode, ANSInsuranceOperator, OperatorCallLog, mascarar_numero_carteira,
)
from .services import enviar_lote, TISSServiceError


@admin.register(TISSOperatorConfig)
class TISSOperatorConfigAdmin(TenantScopedAdminMixin, BaseAdmin):
    # login_encrypted/senha_encrypted NUNCA aparecem em list_display nem em
    # fields — só existem como TextField cifrado, não editável no admin
    # (edição é via os campos write-only `login`/`senha` do serializer da API).
    list_display = ('nome_operadora', 'registro_ans', 'clinic', 'gateway_provider', 'ativo', 'created_at')
    list_filter = ('ativo', 'gateway_provider')
    search_fields = ('nome_operadora', 'registro_ans', 'clinic__name')
    readonly_fields = ('id', 'created_at', 'updated_at', 'capacidades', 'conexao')
    exclude = ('login_encrypted', 'senha_encrypted')
    actions = ['testar_conexao']

    @admin.display(description='Capacidades do provider')
    def capacidades(self, obj):
        """§4.5 — o que este provider suporta. Estático, não faz I/O."""
        if not obj or not obj.pk:
            return '—'
        try:
            caps = providers.capabilities_for(obj)
        except providers.ProviderNaoRegistrado as exc:
            return format_html('<span style="color: red;">{}</span>', str(exc))
        rotulos = {
            'cobertura': 'Cobertura (elegibilidade/autorização)',
            'envio_lote': 'Envio de lote',
            'consulta_status': 'Consulta de status',
            'cancelamento_guia': 'Cancelamento de guia',
        }
        linhas = [
            f'{"✅" if getattr(caps, chave) else "❌"} {rotulo}'
            for chave, rotulo in rotulos.items()
        ]
        linhas.append(f'Versões do padrão: {", ".join(caps.versoes_padrao_suportadas) or "—"}')
        linhas.append(f'{"✅" if caps.confirmado_em_homologacao else "⚠️"} Homologado contra a operadora real')
        return format_html('<br>'.join('{}' for _ in linhas), *linhas)

    @admin.display(description='Última verificação de conexão')
    def conexao(self, obj):
        return 'Use a ação "Testar conexão" na listagem.'

    @admin.action(description='Testar conexão com a operadora (sonda leve, sem dado de paciente)')
    def testar_conexao(self, request, queryset):
        """
        §4.4(b) — sonda ATIVA sob demanda. Nunca dispara uma
        autorização/consulta real: isso teria custo contratual com a
        operadora e usaria dado de beneficiário para uma finalidade que o
        titular não consentiu. Funciona com a operadora desativada (é
        justamente o que se quer checar antes de religar).
        """
        for config in queryset:
            try:
                saude = providers.health_check(config)
            except providers.ProviderNaoRegistrado as exc:
                self.message_user(request, f'{config.nome_operadora}: {exc}', level='ERROR')
                continue
            nivel = 'SUCCESS' if saude.reachable else 'WARNING'
            self.message_user(
                request,
                f'{config.nome_operadora} ({config.registro_ans}): '
                f'{"alcançável" if saude.reachable else "inalcançável"} '
                f'— {saude.latency_ms}ms — {saude.detail}',
                level=nivel,
            )


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
    # BACFF-AVULSA-02: numero_carteira/beneficiario_nome continuam no banco
    # em texto pleno (dado de negócio real, necessário para montar o XML
    # TISS assinado à operadora — diferente de TISSElegibilidadeConsulta,
    # aqui não dá pra mascarar/remover na persistência). O que muda é só a
    # EXIBIÇÃO no admin: quem tem acesso de suporte à clínica vê versão
    # mascarada no formulário de detalhe, nunca o dado completo. Os campos
    # brutos são substituídos pelos métodos mascarados em `fields` — não
    # aparecem em nenhum form editável (guia é gerada pelo sistema, não
    # editada manualmente via admin).
    list_display = ('numero', 'clinic', 'competencia', 'status_colorido', 'valor', 'created_at')
    list_filter = ('status', 'competencia', 'clinic')
    search_fields = ('numero', 'appointment_id', 'clinic__name')
    readonly_fields = (
        'id', 'created_at', 'updated_at',
        'numero_carteira_mascarado', 'beneficiario_nome_mascarado',
    )
    fields = (
        'id', 'clinic', 'lote', 'appointment_id', 'tipo', 'numero', 'competencia', 'status',
        'numero_carteira_mascarado', 'beneficiario_nome_mascarado',
        'procedimentos', 'valor', 'created_at', 'updated_at',
    )

    @admin.display(description='Nº Carteira')
    def numero_carteira_mascarado(self, obj):
        return mascarar_numero_carteira(obj.numero_carteira if obj else '')

    @admin.display(description='Beneficiário')
    def beneficiario_nome_mascarado(self, obj):
        v = obj.beneficiario_nome if obj else ''
        if not v:
            return '—'
        partes = v.split()
        if len(partes) == 1:
            return f'{partes[0][0]}***'
        return f"{partes[0]} {'*' * len(partes[-1])}"

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
    # BACFF-AVULSA-01: só log operacional (sem PII de beneficiário) — o
    # admin do sistema vê se a integração funcionou ou falhou, não quem é o
    # paciente nem se ele é elegível. Somente leitura — imutável após criado.
    list_display = ('clinic', 'origem', 'status', 'created_at')
    list_filter = ('origem', 'status', 'clinic')
    search_fields = ('clinic__name', 'appointment_id')
    readonly_fields = [f.name for f in TISSElegibilidadeConsulta._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(OperatorCallLog)
class OperatorCallLogAdmin(TenantScopedAdminMixin, BaseAdmin):
    """
    §4.4(a) — log passivo de chamadas às operadoras. Append-only e somente
    leitura: é a fonte de verdade da saúde das integrações, não deve ser
    editável nem por suporte.

    Não há nenhum campo com PII aqui por construção (ver docstring do model)
    — só metadado operacional.
    """
    list_display = ('registro_ans', 'gateway_provider', 'operation', 'outcome', 'latency_ms', 'clinic', 'created_at')
    list_filter = ('outcome', 'operation', 'gateway_provider')
    search_fields = ('registro_ans', 'clinic__name')
    readonly_fields = [f.name for f in OperatorCallLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(TUSSProcedureCode)
class TUSSProcedureCodeAdmin(admin.ModelAdmin):
    # Tabela mestre global (sem clinic FK) — quem administra é a equipe
    # SyncroHealth, não TenantScopedAdminMixin.
    list_display = ('tuss_code', 'description', 'table_code', 'updated_at')
    list_filter = ('table_code',)
    search_fields = ('tuss_code', 'description')
    readonly_fields = ('updated_at',)


@admin.register(ANSInsuranceOperator)
class ANSInsuranceOperatorAdmin(admin.ModelAdmin):
    list_display = ('ans_code', 'name', 'cnpj', 'active', 'updated_at')
    list_filter = ('active',)
    search_fields = ('ans_code', 'name', 'cnpj')
    readonly_fields = ('updated_at',)
