from django.contrib import admin
from .models import Feriado

@admin.register(Feriado)
class FeriadoAdmin(admin.ModelAdmin):
    list_display = ('date', 'name', 'description', 'type', 'uf', 'ibge_code', 'year')
    list_filter = ('type', 'uf', 'year')
    search_fields = ('name', 'uf', 'ibge_code')
    ordering = ('-date',)
    date_hierarchy = 'date'
    
    fieldsets = (
        ('Informações Básicas', {
            'fields': ('date', 'name', 'description', 'type', 'year')
        }),
        ('Escopo Territorial (Apenas se Estadual/Municipal)', {
            'fields': ('uf', 'ibge_code'),
        }),
    )
    