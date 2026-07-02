import uuid
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from accounts.models import ClinicAccess
from billing.serializers import InvoiceSerializer, PlanSerializer
from clinics.models import Clinic, ClinicStatus, Plan as ClinicsEnum
from .models import Plan, Invoice, InvoiceStatus
from rest_framework.test import APITestCase

def make_clinic(name='Clínica Teste'):
    """Helper para criar clínica"""
    return Clinic.objects.create(
        name=name,
        slug=f'clinica-{uuid.uuid4().hex[:8]}',
        plan=ClinicsEnum.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
        db_name=f'clinic_{uuid.uuid4().hex[:8]}',
        db_user=f'u_{uuid.uuid4().hex[:8]}',
    )


class PlanModelTest(TestCase):
    """Testes do modelo Plan"""

    def setUp(self):
        self.plan = Plan.objects.create(
            name='Profissional',
            price=Decimal('299.99'),
            max_users=50,
            max_professionals=10,
            active=True,
        )

    def test_create_plan(self):
        """Criar plan"""
        self.assertEqual(self.plan.name, 'Profissional')
        self.assertEqual(self.plan.price, Decimal('299.99'))
        self.assertTrue(self.plan.active)

    def test_plan_unique_name(self):
        """Nome deve ser único"""
        with self.assertRaises(Exception):
            Plan.objects.create(
                name='Profissional',
                price=Decimal('100.00'),
                max_users=10,
                max_professionals=5,
            )

    def test_plan_str(self):
        """String representation"""
        self.assertIn('Profissional', str(self.plan))
        self.assertIn('299.99', str(self.plan))


class InvoiceModelTest(TestCase):
    """Testes do modelo Invoice"""

    def setUp(self):
        self.clinic = make_clinic()
        self.invoice = Invoice.objects.create(
            clinic=self.clinic,
            competencia='2026-06',
            amount=Decimal('299.99'),
            due_date=timezone.now().date(),
            status=InvoiceStatus.PENDING,
        )

    def test_create_invoice(self):
        """Criar invoice"""
        self.assertEqual(self.invoice.status, InvoiceStatus.PENDING)
        self.assertIsNone(self.invoice.paid_at)

    def test_invoice_unique_together(self):
        """Clinic + competencia devem ser únicos"""
        with self.assertRaises(Exception):
            Invoice.objects.create(
                clinic=self.clinic,
                competencia='2026-06',
                amount=Decimal('100.00'),
                due_date=timezone.now().date(),
            )

    def test_mark_invoice_paid(self):
        """Marcar como paga"""
        self.invoice.status = InvoiceStatus.PAID
        self.invoice.paid_at = timezone.now()
        self.invoice.save()

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, InvoiceStatus.PAID)
        self.assertIsNotNone(self.invoice.paid_at)

    def test_invoice_str(self):
        """String representation"""
        self.assertIn(self.clinic.name, str(self.invoice))
        self.assertIn('2026-06', str(self.invoice))
        
class PlanSerializerTest(TestCase):
    """Testes do PlanSerializer"""

    def setUp(self):
        self.plan = Plan.objects.create(
            name='Starter',
            price=Decimal('99.99'),
            max_users=10,
            max_professionals=3,
        )

    def test_serialize_plan(self):
        """Serializar plan"""
        serializer = PlanSerializer(self.plan)
        data = serializer.data

        self.assertEqual(data['name'], 'Starter')
        self.assertEqual(data['price'], '99.99')
        self.assertEqual(data['max_users'], 10)

    def test_plan_readonly_fields(self):
        """ID é read-only"""
        data = {'name': 'New', 'price': '100.00', 'max_users': 5, 'max_professionals': 2}
        serializer = PlanSerializer(data=data)
        self.assertTrue(serializer.is_valid())
        plan = serializer.save()
        self.assertIsNotNone(plan.id)
        
class InvoiceSerializerTest(TestCase):
    """Testes do InvoiceSerializer"""

    def setUp(self):
        self.clinic = make_clinic()
        self.invoice = Invoice.objects.create(
            clinic=self.clinic,
            competencia='2026-07',
            amount=Decimal('500.00'),
            due_date=timezone.now().date(),
        )

    def test_serialize_invoice(self):
        """Serializar invoice"""
        serializer = InvoiceSerializer(self.invoice)
        data = serializer.data

        self.assertEqual(data['competencia'], '2026-07')
        self.assertEqual(data['amount'], '500.00')
        self.assertEqual(data['clinic_name'], self.clinic.name)

    def test_invoice_readonly_fields(self):
        """paid_at é read-only"""
        data = {
            'clinic': str(self.clinic.id),
            'competencia': '2026-08',
            'amount': '300.00',
            'due_date': timezone.now().date(),
        }
        serializer = InvoiceSerializer(data=data)
        self.assertTrue(serializer.is_valid())
        
        
class InvoiceViewSetTest(APITestCase):
    """Testes do InvoiceViewSet"""

    def setUp(self):
        self.clinic = make_clinic()
        self.invoice = Invoice.objects.create(
            clinic=self.clinic,
            competencia='2026-06',
            amount=Decimal('299.99'),
            due_date=timezone.now().date(),
        )
        
        from django.contrib.auth import get_user_model
        User = get_user_model()

        # Criar usuário de teste
        self.user = User.objects.create_user(
            username='testuser',
            email='test@test.com',
            password='testpass123'
        )

        # Autenticar
        self.client.force_authenticate(user=self.user)

        self.clinic = make_clinic()
        self.invoice = Invoice.objects.create(
            clinic=self.clinic,
            competencia='2026-06',
            amount=Decimal('299.99'),
            due_date=timezone.now().date(),
        )

        # Conceder acesso do usuário de teste à clínica (necessário para isolamento de tenant)
        ClinicAccess.objects.create(
            support_user=self.user,
            clinic=self.clinic,
            role=ClinicAccess.AccessRole.VIEWER,
            granted_by=self.user,
        )

    def test_list_invoices(self):
        """GET /api/billing/invoices/"""
        response = self.client.get('/api/billing/invoices/')
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data['results']), 1)

    def test_retrieve_invoice(self):
        """GET /api/billing/invoices/{id}"""
        response = self.client.get(f'/api/billing/invoices/{self.invoice.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['competencia'], '2026-06')

    def test_filter_invoices_by_clinic(self):
        """Filter por clinic"""
        response = self.client.get(f'/api/billing/invoices/?clinic={self.clinic.id}')
        self.assertEqual(response.status_code, 200)

    def test_generate_invoices(self):
        """POST /api/billing/invoices/generate/"""
        response = self.client.post(
            '/api/billing/invoices/generate/',
            {'competencia': '2026-09'},
            format='json'
        )
        self.assertIn(response.status_code, [200, 201])

    def test_mark_invoice_paid(self):
        """PATCH /api/billing/invoices/{id}/mark-paid/"""
        response = self.client.patch(
            f'/api/billing/invoices/{self.invoice.id}/mark-paid/',
            {},
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], InvoiceStatus.PAID)

    def test_mark_overdue(self):
        """POST /api/billing/invoices/mark-overdue/"""
        # Criar invoice vencida
        old_invoice = Invoice.objects.create(
            clinic=self.clinic,
            competencia='2026-01',
            amount=Decimal('100.00'),
            due_date=timezone.now().date() - timezone.timedelta(days=10),
        )

        response = self.client.post(
            '/api/billing/invoices/mark-overdue/',
            {},
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.data['marked_overdue'], 0)