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


class TenantScopedAdminMixin:
    """
    Aplica no Django Admin o mesmo isolamento de tenant já usado nas APIs
    REST (ex: billing/views.py::InvoiceViewSet, tiss/views.py::_allowed_clinic_ids):
    superuser e SupportUser com role ADMIN veem tudo; os demais só veem
    registros de clínicas às quais têm ClinicAccess ativo.

    Sem isso, conceder `view_*` a um grupo não-ADMIN (ex: "Analista
    Operacional", TASK-056) vaza dados — incluindo PHI, no caso do app
    `tiss` — de TODAS as clínicas no /admin/, mesmo a API estando isolada.

    `clinic_lookup` define o caminho de FK até `clinics.Clinic` a partir do
    model do ModelAdmin (ex.: 'clinic', 'guia__clinic') para modelos que não
    têm FK direta. Quando o próprio model do ModelAdmin É `clinics.Clinic`
    (ex.: `ClinicAdmin`), use `clinic_lookup = 'pk'` — o filtro passa a ser
    direto por PK em vez de atravessar uma FK.
    """
    clinic_lookup = 'clinic'

    def get_queryset(self, request):
        qs = super().get_queryset(request)

        user = request.user
        if user.is_superuser:
            return qs
        if getattr(user, 'role', None) == 'admin':
            return qs

        from accounts.models import ClinicAccess
        allowed_clinic_ids = ClinicAccess.objects.filter(
            support_user=user,
            revoked_at__isnull=True,
        ).values_list('clinic_id', flat=True)
        if self.clinic_lookup == 'pk':
            return qs.filter(pk__in=allowed_clinic_ids)
        return qs.filter(**{f'{self.clinic_lookup}__id__in': allowed_clinic_ids})
