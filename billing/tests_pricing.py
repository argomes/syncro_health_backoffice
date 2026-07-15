"""
TASK-BO-11: testes de cobrança recorrente Asaas — plano único R$199,90 com
desconto de lançamento (30 vagas) e desconto de fidelidade (decisão manual).
"""
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from clinics.models import Clinic, ClinicStatus, Plan, PROMOTIONAL_SLOTS
from billing.asaas import AsaasClient
from billing.services import (
    PRICE_PROMOTIONAL,
    PRICE_STANDARD,
    adjust_promotional_pricing,
    create_clinic_subscription,
)


_counter = {'n': 0}


def make_clinic(**kwargs):
    _counter['n'] += 1
    n = _counter['n']
    defaults = dict(
        name='Clínica Teste',
        slug=f"clinica-{n}-{timezone.now().timestamp()}".replace('.', ''),
        plan=Plan.STARTER,
        status=ClinicStatus.ACTIVE,
        cnpj=f'{n:014d}',
        db_name=f'db_test_{n}',
        db_user=f'user_test_{n}',
    )
    defaults.update(kwargs)
    return Clinic.objects.create(**defaults)


class AsaasClientUpdateSubscriptionTests(TestCase):
    def test_update_subscription_value_mock_mode(self):
        client = AsaasClient()
        self.assertTrue(client.is_mock)
        result = client.update_subscription_value('sub_mock_123', 199.90)
        self.assertTrue(result)

    def test_create_subscription_sends_generic_description_and_external_reference(self):
        client = AsaasClient()
        client.is_mock = False
        client.api_key = 'fake-key'

        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {'id': 'sub_real_123'}

        class FakeHttpxClient:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, url, json, headers):
                captured['payload'] = json
                return FakeResponse()

        with patch('billing.asaas.httpx.Client', return_value=FakeHttpxClient()):
            sub_id = client.create_subscription(
                customer_id='cus_real_123',
                value=199.90,
                description='Plano Syncro Health — mensalidade',
                external_reference='11111111-1111-1111-1111-111111111111',
            )

        self.assertEqual(sub_id, 'sub_real_123')
        self.assertEqual(captured['payload']['description'], 'Plano Syncro Health — mensalidade')
        self.assertEqual(captured['payload']['externalReference'], '11111111-1111-1111-1111-111111111111')
        # LGPD: garante que nenhum campo de paciente é enviado
        self.assertNotIn('patient', captured['payload'])
        self.assertNotIn('paciente', str(captured['payload']).lower())


class PromotionalSlotsTests(TestCase):
    def test_slots_available_when_under_limit(self):
        for i in range(5):
            make_clinic(preco_promocional=True, slug=f'promo-{i}')
        self.assertTrue(Clinic.promotional_slots_available())

    def test_slots_unavailable_at_limit(self):
        for i in range(PROMOTIONAL_SLOTS):
            make_clinic(preco_promocional=True, slug=f'promo-full-{i}')
        self.assertFalse(Clinic.promotional_slots_available())

    def test_model_clean_blocks_31st_promotional_clinic(self):
        for i in range(PROMOTIONAL_SLOTS):
            make_clinic(preco_promocional=True, slug=f'promo-clean-{i}')

        clinic_31 = Clinic(
            name='Clínica 31',
            slug='clinica-31',
            preco_promocional=True,
        )
        with self.assertRaises(ValidationError):
            clinic_31.clean()

    def test_model_clean_allows_editing_existing_promotional_clinic(self):
        """Uma clínica já promocional pode ser salva de novo sem contar a si mesma."""
        for i in range(PROMOTIONAL_SLOTS):
            make_clinic(preco_promocional=True, slug=f'promo-edit-{i}')

        existing = Clinic.objects.filter(preco_promocional=True).first()
        existing.name = 'Nome Atualizado'
        # Não deve levantar, pois exclui a si mesma da contagem
        existing.clean()


class CreateClinicSubscriptionTests(TestCase):
    def test_create_subscription_standard_price(self):
        clinic = make_clinic(cnpj='11.111.111/0001-11')
        create_clinic_subscription(clinic)
        clinic.refresh_from_db()
        self.assertIsNotNone(clinic.asaas_subscription_id)
        self.assertIsNotNone(clinic.subscription_started_at)

    def test_create_subscription_promotional_price(self):
        clinic = make_clinic(cnpj='22.222.222/0001-22', preco_promocional=True)
        create_clinic_subscription(clinic)
        clinic.refresh_from_db()
        self.assertIsNotNone(clinic.asaas_subscription_id)

    def test_create_subscription_blocked_when_slots_full(self):
        for i in range(PROMOTIONAL_SLOTS):
            c = make_clinic(preco_promocional=True, slug=f'promo-sub-{i}')
            create_clinic_subscription(c)

        clinic_31 = make_clinic(preco_promocional=True, slug='promo-sub-31')
        with self.assertRaises(ValueError):
            create_clinic_subscription(clinic_31)


class AdjustPromotionalPricingJobTests(TestCase):
    """Cobre os 5 cenários do acceptance criteria do job de reajuste."""

    def _promotional_clinic(self, months_ago_days, **kwargs):
        started = timezone.now() - timezone.timedelta(days=months_ago_days)
        return make_clinic(
            preco_promocional=True,
            subscription_started_at=started,
            asaas_subscription_id='sub_mock_test',
            **kwargs,
        )

    def test_new_subscription_not_adjusted(self):
        clinic = self._promotional_clinic(months_ago_days=1, slug='new-sub')
        result = adjust_promotional_pricing()
        clinic.refresh_from_db()
        self.assertIsNone(clinic.price_adjusted_at)
        self.assertEqual(result['adjusted'], 0)

    def test_eleven_months_not_adjusted(self):
        clinic = self._promotional_clinic(months_ago_days=11 * 30, slug='eleven-months')
        adjust_promotional_pricing()
        clinic.refresh_from_db()
        self.assertIsNone(clinic.price_adjusted_at)

    def test_twelve_months_without_loyalty_is_adjusted(self):
        clinic = self._promotional_clinic(
            months_ago_days=366, slug='twelve-months-no-loyalty',
            desconto_fidelidade_ano2=False,
        )
        with patch.object(AsaasClient, 'update_subscription_value', return_value=True) as mock_update:
            result = adjust_promotional_pricing()

        clinic.refresh_from_db()
        self.assertIsNotNone(clinic.price_adjusted_at)
        mock_update.assert_called_once_with('sub_mock_test', float(PRICE_STANDARD))
        self.assertEqual(result['adjusted'], 1)
        self.assertEqual(result['kept_loyalty'], 0)

    def test_twelve_months_with_loyalty_keeps_price(self):
        clinic = self._promotional_clinic(
            months_ago_days=366, slug='twelve-months-loyalty',
            desconto_fidelidade_ano2=True,
        )
        with patch.object(AsaasClient, 'update_subscription_value') as mock_update:
            result = adjust_promotional_pricing()

        clinic.refresh_from_db()
        self.assertIsNotNone(clinic.price_adjusted_at)
        mock_update.assert_not_called()
        self.assertEqual(result['adjusted'], 0)
        self.assertEqual(result['kept_loyalty'], 1)

    def test_already_processed_clinic_is_not_reprocessed(self):
        clinic = self._promotional_clinic(
            months_ago_days=366, slug='already-processed',
            price_adjusted_at=timezone.now() - timezone.timedelta(days=1),
        )
        with patch.object(AsaasClient, 'update_subscription_value') as mock_update:
            result = adjust_promotional_pricing()

        mock_update.assert_not_called()
        self.assertEqual(result['adjusted'], 0)
        self.assertEqual(result['kept_loyalty'], 0)

    def test_management_command_runs_job(self):
        from django.core.management import call_command
        from io import StringIO

        self._promotional_clinic(
            months_ago_days=366, slug='cmd-test', desconto_fidelidade_ano2=False,
        )
        out = StringIO()
        with patch.object(AsaasClient, 'update_subscription_value', return_value=True):
            call_command('adjust_promotional_pricing', stdout=out)
        self.assertIn('Reajuste concluído', out.getvalue())
