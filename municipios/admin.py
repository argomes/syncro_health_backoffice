from django.contrib import admin

from .models import Municipio


@admin.register(Municipio)
class MunicipioAdmin(admin.ModelAdmin):
    list_display = ('codigo_ibge', 'nome', 'uf')
    search_fields = ('codigo_ibge', 'nome')
    list_filter = ('uf',)
    ordering = ('nome',)
