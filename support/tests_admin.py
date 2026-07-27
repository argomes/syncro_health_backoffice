"""
Prova de isolamento de tenant no Django Admin do app support (Ticket,
TicketMessage) — mesmo gap já corrigido em tiss/admin.py e clinics/admin.py.

Usa o grupo real "Analista Operacional" (seed_admin_groups), que hoje já tem
view/add/change em Ticket e TicketMessage concedidos em produção — sem o
TenantScopedAdminMixin, esse grupo vazaria tickets de todas as clínicas.
"""
import uuid
from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db.models.signals import post_save
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import ClinicAccess, SupportUser
from clinics.models import Clinic, ClinicStatus, Plan

from .models import Ticket, TicketMessage, sync_ticket_to_zoho


def make_clinic(**overrides):
    defaults = dict(
        name='Clínica Teste Support',
        slug=f'clinica-{uuid.uuid4().hex[:8]}',
        plan=Plan.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
        db_name=f'clinic_{uuid.uuid4().hex[:8]}',
        db_user=f'u_{uuid.uuid4().hex[:8]}',
    )
    defaults.update(overrides)
    return Clinic.objects.create(**defaults)


def seed_groups():
    out = StringIO()
    call_command('seed_admin_groups', stdout=out)
    return out.getvalue()


class SupportAdminTenantScopingTest(TestCase):
    @classmethod
    def setUpClass(cls):
        # Desconecta o signal que enfileira sync_ticket_to_zoho — sem isso,
        # com CELERY_TASK_ALWAYS_EAGER=True (default local quando DEBUG=True),
        # cada Ticket.objects.create() chama a API real do Zoho Desk.
        super().setUpClass()
        post_save.disconnect(sync_ticket_to_zoho, sender=Ticket)

    def setUp(self):
        seed_groups()
        self.clinic_a = make_clinic(name='Clínica Support A')
        self.clinic_b = make_clinic(name='Clínica Support B')

        self.ticket_a = Ticket.objects.create(
            clinic=self.clinic_a, title='Problema sigiloso A', description='desc A',
        )
        self.ticket_b = Ticket.objects.create(
            clinic=self.clinic_b, title='Problema sigiloso B', description='desc B',
        )
        self.message_a = TicketMessage.objects.create(ticket=self.ticket_a, message='mensagem A')
        self.message_b = TicketMessage.objects.create(ticket=self.ticket_b, message='mensagem B')

        self.analyst = SupportUser.objects.create_user(
            username='analista_support', email='analista_support@syncro.test',
            password='senha-teste-123', is_staff=True,
        )
        self.analyst.groups.add(Group.objects.get(name='Analista Operacional'))
        ClinicAccess.objects.create(support_user=self.analyst, clinic=self.clinic_a, role='viewer')

        self.client = Client()
        self.client.force_login(self.analyst)

    def test_analyst_does_not_see_other_clinic_ticket(self):
        url = reverse('admin:support_ticket_changelist')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Problema sigiloso A')
        self.assertNotContains(response, 'Problema sigiloso B')

    def test_analyst_cannot_open_other_clinic_ticket_detail(self):
        url = reverse('admin:support_ticket_change', args=[self.ticket_b.pk])
        response = self.client.get(url)
        self.assertNotEqual(response.status_code, 200)

    def test_analyst_does_not_see_other_clinic_ticket_message(self):
        """TicketMessage não tem FK direta a Clinic — isolamento via ticket__clinic."""
        url = reverse('admin:support_ticketmessage_changelist')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'mensagem A')
        self.assertNotContains(response, 'mensagem B')

    def test_superuser_sees_all_tickets_and_messages(self):
        superuser = SupportUser.objects.create_superuser(
            username='root_support', email='root_support@syncro.test', password='senha-teste-123',
        )
        client = Client()
        client.force_login(superuser)

        response = client.get(reverse('admin:support_ticket_changelist'))
        self.assertContains(response, 'Problema sigiloso A')
        self.assertContains(response, 'Problema sigiloso B')

        response = client.get(reverse('admin:support_ticketmessage_changelist'))
        self.assertContains(response, 'mensagem A')
        self.assertContains(response, 'mensagem B')
