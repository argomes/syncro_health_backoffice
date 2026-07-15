from django.contrib import admin, messages
from syncro_backoffice.base_admin import BaseAdmin
from .models import Clinic, PROMOTIONAL_SLOTS


@admin.register(Clinic)
class ClinicAdmin(BaseAdmin):
    # TASK-056: license_key, public_key_pem e db_password_encrypted são
    # segredos de credenciamento/criptografia da clínica — nunca expostos a
    # grupos não-superuser (ex.: Analista Operacional), mesmo tendo
    # view_clinic. Ver get_list_display/get_fields abaixo.
    SENSITIVE_FIELDS = ('license_key', 'public_key_pem', 'db_password_encrypted')

    # TASK-BO-11: gestão de desconto/reajuste é admin-only. Nenhum desses
    # campos é exposto em API/tela do portal_gestor da clínica.
    list_display = (
        'name', 'plan', 'status', 'license_key',
        'preco_promocional', 'desconto_fidelidade_ano2', 'created_at',
    )
    list_filter = ('plan', 'status', 'preco_promocional', 'desconto_fidelidade_ano2')
    search_fields = ('name', 'slug', 'contact_email')
    readonly_fields = (
        'id', 'license_key', 'created_at', 'updated_at',
        'price_adjusted_at', 'promotional_slots_info',
    )
    actions = ['suspend_clinics', 'activate_clinics', 'create_asaas_subscription']

    @admin.display(description='Vagas de desconto de lançamento')
    def promotional_slots_info(self, obj=None):
        used = Clinic.objects.filter(preco_promocional=True).count()
        return f'{used}/{PROMOTIONAL_SLOTS} vagas promocionais usadas'

    def get_list_display(self, request):
        list_display = super().get_list_display(request)
        if request.user.is_superuser:
            return list_display
        return tuple(f for f in list_display if f not in self.SENSITIVE_FIELDS)

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        if request.user.is_superuser:
            return fields
        return [f for f in fields if f not in self.SENSITIVE_FIELDS]

    def get_readonly_fields(self, request, obj=None):
        readonly = super().get_readonly_fields(request, obj)
        if request.user.is_superuser:
            return readonly
        return tuple(f for f in readonly if f not in self.SENSITIVE_FIELDS)

    @admin.action(description='Suspender selecionadas')
    def suspend_clinics(self, request, queryset):
        queryset.update(status=Clinic.ClinicStatus.SUSPENDED if hasattr(Clinic, 'ClinicStatus') else 'suspended')

    @admin.action(description='Ativar selecionadas')
    def activate_clinics(self, request, queryset):
        queryset.update(status='active')

    @admin.action(description='Criar assinatura Asaas (TASK-BO-11)')
    def create_asaas_subscription(self, request, queryset):
        from billing.services import create_clinic_subscription

        created, failed = 0, 0
        for clinic in queryset:
            if clinic.asaas_subscription_id:
                self.message_user(
                    request,
                    f'{clinic.name}: já possui assinatura Asaas, ignorada.',
                    level=messages.WARNING,
                )
                continue
            try:
                create_clinic_subscription(clinic)
                created += 1
            except Exception as exc:
                failed += 1
                self.message_user(
                    request, f'{clinic.name}: erro ao criar assinatura — {exc}',
                    level=messages.ERROR,
                )
        if created:
            self.message_user(request, f'{created} assinatura(s) criada(s) com sucesso.', level=messages.SUCCESS)
