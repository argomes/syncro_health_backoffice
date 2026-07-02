"""
Base admin classes para o Syncro Backoffice.

Centraliza a herança de unfold.admin.ModelAdmin e os formfield_overrides
padrão para que todos os ModelAdmin do projeto apliquem os widgets do
Unfold automaticamente em campos CharField e TextField.
"""
from django.db import models
from unfold.admin import ModelAdmin, TabularInline, StackedInline
from unfold.widgets import UnfoldAdminTextareaWidget, UnfoldAdminTextInputWidget


class BaseAdmin(ModelAdmin):
    """
    ModelAdmin base para todos os apps do backoffice.

    Garante que:
    - Todos os formulários usem os widgets estilizados do Unfold.
    - A herança de unfold.admin.ModelAdmin seja consistente em todo o projeto.
    """
    formfield_overrides = {
        models.CharField: {"widget": UnfoldAdminTextInputWidget},
        models.TextField: {"widget": UnfoldAdminTextareaWidget},
    }


class BaseTabularInline(TabularInline):
    """TabularInline base com widgets Unfold."""
    formfield_overrides = {
        models.CharField: {"widget": UnfoldAdminTextInputWidget},
        models.TextField: {"widget": UnfoldAdminTextareaWidget},
    }


class BaseStackedInline(StackedInline):
    """StackedInline base com widgets Unfold."""
    formfield_overrides = {
        models.CharField: {"widget": UnfoldAdminTextInputWidget},
        models.TextField: {"widget": UnfoldAdminTextareaWidget},
    }
