from django.contrib import admin

from .models import Cep


@admin.register(Cep)
class CepAdmin(admin.ModelAdmin):
    list_display = ('cep', 'logradouro', 'bairro', 'localidade', 'uf')
    search_fields = ('cep', 'logradouro', 'bairro')
    list_filter = ('uf',)
    ordering = ('logradouro',)
