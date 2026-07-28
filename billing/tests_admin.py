"""
ADMIN-DASHBOARD-REDESIGN — billing.Plan/billing.Invoice não estavam
registrados no Django admin (billing/admin.py era o arquivo vazio gerado
pelo startapp). Prova que agora existe tela para os dois e que Invoice
segue o mesmo isolamento por tenant (ClinicAccess) que o resto do sistema.
"""
import uuid
from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import ClinicAccess, SupportUser
from clinics.models import Clinic, ClinicStatus
from clinics.models import Plan as ClinicPlanChoice

from .admin import InvoiceAdmin, PlanAdmin
from .models import Invoice, InvoiceStatus, Plan


def _make_clinic(slug):
    return Clinic.objects.create(
        name=f'Clínica {slug}', slug=slug,
        plan=ClinicPlanChoice.PROFESSIONAL, status=ClinicStatus.ACTIVE,
        cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
        db_name=f'db_{uuid.uuid4().hex[:8]}', db_user=f'u_{uuid.uuid4().hex[:8]}',
    )


def _make_invoice(clinic, competencia='2026-07', status=InvoiceStatus.PENDING):
    return Invoice.objects.create(
        clinic=clinic, competencia=competencia, amount=Decimal('299.90'),
        status=status, due_date=timezone.now().date(),
    )


class InvoiceAndPlanRegisteredInAdminTest(TestCase):
    """Prova via HTTP que as duas telas existem e respondem 200 pro superuser."""

    def setUp(self):
        self.client = Client()
        self.superuser = SupportUser.objects.create_superuser(
            username='root-billing', email='root-billing@syncro.test', password='senha-teste-123',
        )
        self.client.force_login(self.superuser)

    def test_plan_changelist_reachable(self):
        response = self.client.get(reverse('admin:billing_plan_changelist'))
        self.assertEqual(response.status_code, 200)

    def test_invoice_changelist_reachable(self):
        response = self.client.get(reverse('admin:billing_invoice_changelist'))
        self.assertEqual(response.status_code, 200)

    def test_invoice_changelist_shows_created_invoice(self):
        clinic = _make_clinic('fatura-visivel')
        _make_invoice(clinic)
        response = self.client.get(reverse('admin:billing_invoice_changelist'))
        self.assertContains(response, clinic.name)


class InvoiceAdminTenantScopingTest(TestCase):
    """
    InvoiceAdmin usa TenantScopedAdminMixin (default clinic_lookup='clinic',
    e Invoice tem FK direta 'clinic') — um SupportUser não-admin só pode ver
    faturas das clínicas às quais tem ClinicAccess ativo.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.clinic_a = _make_clinic('clinic-a-fatura')
        self.clinic_b = _make_clinic('clinic-b-fatura')
        self.invoice_a = _make_invoice(self.clinic_a, competencia='2026-01')
        self.invoice_b = _make_invoice(self.clinic_b, competencia='2026-02')

        self.scoped_user = SupportUser.objects.create_user(
            username='analista-billing', email='analista-billing@syncro.test', password='x',
        )
        ClinicAccess.objects.create(support_user=self.scoped_user, clinic=self.clinic_a)

    def test_scoped_user_only_sees_invoices_of_accessible_clinic(self):
        request = self.factory.get('/admin/billing/invoice/')
        request.user = self.scoped_user

        admin_instance = InvoiceAdmin(Invoice, self.site)
        qs = admin_instance.get_queryset(request)

        self.assertIn(self.invoice_a, qs)
        self.assertNotIn(self.invoice_b, qs)

    def test_superuser_sees_all_invoices(self):
        superuser = SupportUser.objects.create_superuser(
            username='root-billing2', email='root-billing2@syncro.test', password='x',
        )
        request = self.factory.get('/admin/billing/invoice/')
        request.user = superuser

        admin_instance = InvoiceAdmin(Invoice, self.site)
        qs = admin_instance.get_queryset(request)

        self.assertIn(self.invoice_a, qs)
        self.assertIn(self.invoice_b, qs)
