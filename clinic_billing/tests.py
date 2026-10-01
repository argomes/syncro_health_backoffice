"""
TASK-BO-R07 — regressão de que o app está registrado e no namespace certo.

Não testa comportamento de negócio (não há model ainda) — só garante que a
decisão de nome (clinic_billing != billing) não regride silenciosamente, e que
o app `billing` (SaaS da Syncro) continua existindo sem ganhar nada de
faturamento clínico por engano.
"""
from django.apps import apps
from django.test import SimpleTestCase


class ClinicBillingNamespaceTests(SimpleTestCase):
    def test_clinic_billing_app_is_registered(self):
        self.assertTrue(apps.is_installed('clinic_billing'))

    def test_clinic_billing_app_label_is_distinct_from_saas_billing(self):
        clinic_billing_config = apps.get_app_config('clinic_billing')
        saas_billing_config = apps.get_app_config('billing')
        self.assertNotEqual(clinic_billing_config.label, saas_billing_config.label)
        self.assertEqual(clinic_billing_config.name, 'clinic_billing')
        self.assertEqual(saas_billing_config.name, 'billing')

    def test_saas_billing_app_has_no_clinic_billing_models_yet(self):
        # billing/ é faturamento SaaS da Syncro (Plan/Invoice de assinatura).
        # Nenhum model de faturamento clínico (ex: BillingEntry) deve existir
        # lá — isso indicaria a colisão de nome que esta task resolveu voltando.
        saas_billing_models = {m.__name__ for m in apps.get_app_config('billing').get_models()}
        self.assertNotIn('BillingEntry', saas_billing_models)
