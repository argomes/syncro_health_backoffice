"""
Guarda-corpo contra regressão do padrão de bug "ModelAdmin ligado a Clinic
sem TenantScopedAdminMixin" (IDOR cross-tenant no Django Admin).

Esse exato gap de segurança apareceu 3 vezes nessa sessão, em apps
diferentes, corrigidos separadamente:
1. tiss/admin.py (TISSOperatorConfigAdmin, TISSLoteAdmin, TISSGuiaAdmin,
   TISSGlosaAdmin) — triagem de segurança do PR #9.
2. clinics/admin.py (ClinicAdmin) — durante TASK-BO-11 (Asaas).
3. Varredura geral: mais 8 ModelAdmin (SystemHeartbeatAdmin, SystemLogAdmin,
   ReportSessionAdmin, TicketAdmin, TicketMessageAdmin, ClinicAccessAdmin,
   ClinicUserAdmin, ClinicUserNoticeDismissalAdmin).

Este teste itera TODO o admin registry do projeto (todos os apps) e garante
que qualquer model com um caminho de FK (direto ou indireto, até uma
profundidade razoável) até clinics.Clinic tenha seu ModelAdmin usando
TenantScopedAdminMixin (syncro_backoffice/base_admin.py) — sem depender de
alguém lembrar de fazer a varredura manual de novo.
"""
from django.apps import apps
from django.contrib import admin
from django.test import SimpleTestCase

from syncro_backoffice.base_admin import BaseAdmin, TenantScopedAdminMixin

MAX_DEPTH = 3

# Allowlist explícita de ModelAdmin ligados (ou não) a Clinic que são
# intencionalmente isentos do mixin. Cada entrada precisa de justificativa.
ALLOWLIST = {
    # Notificação de produto é global por design (broadcast pra todas as
    # clínicas ao mesmo tempo) — não tem FK real de clínica, embora esteja
    # no mesmo app (portal_gestor) que outros models tenant-scoped.
    "ProductNoticeAdmin",
    # SupportUser é staff interno do backoffice (nosso time de suporte),
    # não é um dado pertencente a uma clínica — não tem e não deve ter FK
    # para Clinic.
    "SupportUserAdmin",
}


def _find_clinic_path(model, _visited=None, _depth=0):
    """
    Procura, recursivamente via FK/O2O, um caminho de `model` até
    clinics.Clinic. Retorna uma lista de nomes de campo representando o
    caminho (ex: ['clinic'] ou ['guia', 'clinic']) ou None se não achar
    dentro de MAX_DEPTH níveis.
    """
    Clinic = apps.get_model("clinics", "Clinic")
    if model is Clinic or model._meta.concrete_model is Clinic:
        return []

    if _visited is None:
        _visited = set()
    if model in _visited or _depth >= MAX_DEPTH:
        return None
    _visited = _visited | {model}

    for field in model._meta.get_fields():
        if not getattr(field, "is_relation", False):
            continue
        # Só seguimos FK/O2O "pra frente" (esse model tem a coluna),
        # ignorando relações reversas (related_name de outros models) para
        # não explodir em falsos positivos/ciclos.
        if not (field.many_to_one or field.one_to_one):
            continue
        if not getattr(field, "concrete", False):
            continue

        related_model = field.related_model
        if related_model is None:
            continue

        if related_model is Clinic:
            return [field.name]

        sub_path = _find_clinic_path(related_model, _visited, _depth + 1)
        if sub_path is not None:
            return [field.name] + sub_path

    return None


class AdminTenantIsolationTests(SimpleTestCase):
    def test_todo_modeladmin_ligado_a_clinic_usa_tenant_scoped_mixin(self):
        """
        Para cada ModelAdmin registrado em django.contrib.admin.site,
        se o model correspondente tem um caminho de FK até clinics.Clinic,
        o ModelAdmin DEVE herdar TenantScopedAdminMixin — a menos que
        esteja na ALLOWLIST explícita acima.
        """
        failures = []

        for model, model_admin in admin.site._registry.items():
            admin_class_name = type(model_admin).__name__

            if admin_class_name in ALLOWLIST:
                continue

            clinic_path = _find_clinic_path(model)
            if clinic_path is None:
                continue

            if not isinstance(model_admin, TenantScopedAdminMixin):
                failures.append(
                    f"{admin_class_name} (model {model._meta.app_label}."
                    f"{model.__name__}) tem caminho até clinics.Clinic via "
                    f"'{'.'.join(clinic_path)}' mas NÃO herda "
                    f"TenantScopedAdminMixin. Isso é um IDOR cross-tenant "
                    f"no Django Admin — adicione o mixin (ver "
                    f"syncro_backoffice/base_admin.py) ou, se for "
                    f"intencional, adicione à ALLOWLIST em "
                    f"syncro_backoffice/tests_admin_tenant_isolation.py "
                    f"com um comentário explicando o motivo."
                )

        if failures:
            self.fail(
                "ModelAdmin(s) ligados a Clinic sem TenantScopedAdminMixin "
                "(IDOR cross-tenant no /admin/):\n- "
                + "\n- ".join(failures)
            )

    def test_deteccao_pega_modeladmin_sem_mixin_registrado_de_proposito(self):
        """
        Sanity check negativo: registra temporariamente um ModelAdmin real
        (ClinicAccessAdmin, que tem FK direta pra Clinic) SEM o mixin, sob
        um model diferente já registrado no admin (reaproveita o mesmo
        model 'clinics.Clinic' via um ModelAdmin substituto fake) e
        confirma que a função de detecção realmente barra isso — prova que
        o teste acima não é um "sempre passa" vazio.
        """
        Clinic = apps.get_model("clinics", "Clinic")

        # Cria um ModelAdmin fake, ligado a Clinic diretamente (é o próprio
        # model Clinic), SEM TenantScopedAdminMixin.
        class _FakeUnsafeClinicAdmin(BaseAdmin):
            pass

        # Clinic já está registrado (ClinicAdmin) — usamos um proxy model
        # in-memory só pra esse teste, sem migração, sem tocar no admin
        # real de Clinic.
        class _FakeUnsafeClinicProxy(Clinic):
            class Meta:
                proxy = True
                app_label = "clinics"
                verbose_name = "Fake Unsafe Clinic Proxy (teste)"

        admin.site.register(_FakeUnsafeClinicProxy, _FakeUnsafeClinicAdmin)
        try:
            clinic_path = _find_clinic_path(_FakeUnsafeClinicProxy)
            self.assertEqual(
                clinic_path,
                [],
                "A função de detecção deveria reconhecer o proxy de Clinic "
                "como o próprio Clinic (caminho vazio).",
            )

            registered_admin = admin.site._registry[_FakeUnsafeClinicProxy]
            self.assertFalse(
                isinstance(registered_admin, TenantScopedAdminMixin),
                "Setup do teste está errado: o ModelAdmin fake não deveria "
                "ter o mixin.",
            )

            # Reproduz a mesma lógica de falha do teste principal, isolada,
            # pra provar que ela detectaria esse caso.
            seria_falha = (
                clinic_path is not None
                and not isinstance(registered_admin, TenantScopedAdminMixin)
                and type(registered_admin).__name__ not in ALLOWLIST
            )
            self.assertTrue(
                seria_falha,
                "A lógica de detecção NÃO pegou um ModelAdmin ligado a "
                "Clinic sem o mixin — o teste principal está vazando "
                "(falso negativo).",
            )
        finally:
            admin.site.unregister(_FakeUnsafeClinicProxy)
