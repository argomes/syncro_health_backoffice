"""Admin do calendário de feriados (Configurações → Feriados).

Herda de `BaseAdmin` (unfold.admin.ModelAdmin): com o `admin.ModelAdmin`
padrão do Django, o template de changelist do Unfold não renderiza o botão
"Adicionar" quando a lista já tem registros — o fundador via os feriados
vindos da API mas não tinha como cadastrar os municipais que faltavam.
"""
from django.contrib import admin

from syncro_backoffice.base_admin import BaseAdmin

from .models import Feriado, FeriadoBusca


@admin.register(Feriado)
class FeriadoAdmin(BaseAdmin):
    """Cadastro manual + consulta dos feriados em cache.

    Dado público (calendário oficial), sem PII e sem vínculo com tenant —
    por isso não usa `TenantScopedAdminMixin`.
    """

    list_display = ('date', 'name', 'type', 'uf', 'ibge_code', 'year')
    list_filter = ('type', 'uf', 'year')
    search_fields = ('name', 'uf', 'ibge_code')
    ordering = ('-date',)
    date_hierarchy = 'date'
    # `year` é derivado da data em Feriado.clean()/save(); expor no form
    # permitiria um ano divergente da data, que some do calendário do gateway.
    readonly_fields = ('year',)

    fieldsets = (
        ('Informações básicas', {
            'fields': ('date', 'name', 'type', 'description'),
        }),
        ('Localidade (estadual/municipal)', {
            'description': (
                'Estadual: informe a UF. Municipal: informe o código IBGE do '
                'município (Ex.: Osasco = 3534401) — a UF é preenchida sozinha. '
                'Nacional: deixe em branco.'
            ),
            'fields': ('uf', 'ibge_code'),
        }),
    )

    def get_fieldsets(self, request, obj=None):  # type: ignore[no-untyped-def]
        """Mostra o ano (somente leitura) apenas na edição, onde já existe."""
        fieldsets = super().get_fieldsets(request, obj)
        if obj is None:
            return fieldsets
        return (*fieldsets, ('Calculado', {'fields': ('year',)}))


@admin.register(FeriadoBusca)
class FeriadoBuscaAdmin(BaseAdmin):
    """Controle do cache: apagar uma linha força nova busca na API para o ibge/ano."""

    list_display = ('ibge_code', 'year', 'fetched_at')
    list_filter = ('year',)
    search_fields = ('ibge_code',)
