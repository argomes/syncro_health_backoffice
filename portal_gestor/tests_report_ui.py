"""
Testes da TASK-050 — UI de geração/consulta de relatório (Django Templates + HTMX).

Reaproveita o padrão de mock já usado em tests_report_views.py (API): a lógica
de negócio de report_reads/services já está testada lá e em
tests_report_reads.py — aqui cobrimos só a camada de apresentação (form,
polling, tratamento de erro amigável).
"""
import uuid
import zoneinfo
from datetime import time, timedelta
from unittest.mock import patch

from django.test import Client, TestCase
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from accounts.models import ClinicUser
from clinics.models import Clinic, ClinicStatus, Plan

from . import report_reads, services
from .models import ReportSession, ReportSessionStatus
from .template_views import ReportNewView


def make_clinic(name='Clínica Teste'):
    return Clinic.objects.create(
        name=name,
        slug=f'clinica-{uuid.uuid4().hex[:8]}',
        plan=Plan.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
        db_name=f'clinic_{uuid.uuid4().hex[:8]}',
        db_user=f'u_{uuid.uuid4().hex[:8]}',
    )


def make_clinic_user(clinic, email='gerente@a.com', password='senha-123'):
    user = ClinicUser(clinic=clinic, email=email, name='Gerente')
    user.set_password(password)
    user.save()
    return user


class ReportNewViewTest(TestCase):
    def setUp(self):
        self.clinic_a = make_clinic('Clínica A')
        self.clinic_b = make_clinic('Clínica B')
        self.user_a = make_clinic_user(self.clinic_a, email='gerente@a.com')
        self.user_b = make_clinic_user(self.clinic_b, email='gerente@b.com')
        self.client.post('/portal/login/', {'email': 'gerente@a.com', 'password': 'senha-123'})

    def test_form_page_renders(self):
        response = self.client.get('/portal/relatorios/novo/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Gerar Relatório')

    def test_requires_login(self):
        self.client.cookies.clear()
        response = self.client.get('/portal/relatorios/novo/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/portal/login/'))

    def test_submit_creates_session_and_redirects_to_status(self):
        response = self.client.post('/portal/relatorios/novo/', {
            'date_from': '2026-07-01',
            'date_to': '2026-07-10',
            'entities': ['patients', 'appointments'],
        })
        session = ReportSession.objects.get()
        self.assertRedirects(response, f'/portal/relatorios/{session.session_id}/', fetch_redirect_response=False)
        self.assertEqual(session.clinic, self.clinic_a)
        self.assertEqual(session.created_by, self.user_a)
        self.assertEqual(session.status, ReportSessionStatus.PENDING)

    def test_submit_without_entities_shows_error_no_session_created(self):
        response = self.client.post('/portal/relatorios/novo/', {
            'date_from': '2026-07-01',
            'date_to': '2026-07-10',
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Selecione ao menos um tipo de dado', status_code=400)
        self.assertFalse(ReportSession.objects.exists())

    def test_submit_with_date_from_after_date_to_shows_error(self):
        response = self.client.post('/portal/relatorios/novo/', {
            'date_from': '2026-07-10',
            'date_to': '2026-07-01',
            'entities': ['patients'],
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ReportSession.objects.exists())

    def test_submit_with_malformed_date_shows_error_not_500(self):
        response = self.client.post('/portal/relatorios/novo/', {
            'date_from': 'not-a-date',
            'date_to': '2026-07-10',
            'entities': ['patients'],
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ReportSession.objects.exists())

    def test_submit_without_csrf_token_is_rejected(self):
        """
        ReportNewView passa por CsrfViewMiddleware (global) + csrf_protect
        explícito (adicionado nesta revisão, mesmo padrão de
        PortalLoginView/PortalRefreshView). django.test.Client desliga a
        checagem de CSRF por padrão — aqui usamos um Client com
        enforce_csrf_checks=True para provar que a proteção está ativa de
        fato, e não só "tecnicamente presente porque o middleware é global".
        """
        strict_client = Client(enforce_csrf_checks=True)
        # Login também é csrf_protect — precisa do token válido pra autenticar
        # antes de exercitar a checagem em ReportNewView especificamente.
        strict_client.get('/portal/login/')
        login_csrf_token = strict_client.cookies['csrftoken'].value
        strict_client.post('/portal/login/', {
            'email': 'gerente@a.com', 'password': 'senha-123',
            'csrfmiddlewaretoken': login_csrf_token,
        }, HTTP_X_CSRFTOKEN=login_csrf_token)

        response = strict_client.post('/portal/relatorios/novo/', {
            'date_from': '2026-07-01',
            'date_to': '2026-07-10',
            'entities': ['patients'],
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ReportSession.objects.exists())

    def test_parse_date_uses_project_local_timezone_not_utc(self):
        """
        `_parse_date` monta um datetime naive e chama timezone.make_aware sem
        tzinfo explícito — isso usa timezone.get_current_timezone(), que por
        sua vez cai no TIME_ZONE do settings (America/Sao_Paulo) quando não
        há timezone ativada por request/middleware, exatamente o fuso do
        gestor da clínica. Confirma que "13/07/2026" vira meia-noite local
        (America/Sao_Paulo), não meia-noite UTC — se fosse UTC, o `date_from`
        apareceria como 12/07 21:00 no horário local, um dia adiantado/atrasado
        do que o usuário digitou.
        """
        date_from = ReportNewView._parse_date('2026-07-13', time.min)
        local = date_from.astimezone(zoneinfo.ZoneInfo('America/Sao_Paulo'))
        self.assertEqual((local.year, local.month, local.day), (2026, 7, 13))
        self.assertEqual((local.hour, local.minute, local.second), (0, 0, 0))

    def test_parse_date_to_covers_full_local_day_until_23_59_59(self):
        date_to = ReportNewView._parse_date('2026-07-13', time.max)
        local = date_to.astimezone(zoneinfo.ZoneInfo('America/Sao_Paulo'))
        self.assertEqual((local.year, local.month, local.day), (2026, 7, 13))
        self.assertEqual((local.hour, local.minute, local.second), (23, 59, 59))


class ReportStatusViewTest(TestCase):
    def setUp(self):
        self.clinic_a = make_clinic('Clínica A')
        self.clinic_b = make_clinic('Clínica B')
        self.user_a = make_clinic_user(self.clinic_a, email='gerente@a.com')
        make_clinic_user(self.clinic_b, email='gerente@b.com')
        self.client.post('/portal/login/', {'email': 'gerente@a.com', 'password': 'senha-123'})

        self.session = services.create_report_session(
            clinic=self.clinic_a, created_by=self.user_a, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )

    def test_pending_status_shows_waiting_message_and_polls(self):
        response = self.client.get(f'/portal/relatorios/{self.session.session_id}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Aguardando sincronização')
        self.assertContains(response, 'hx-trigger="every 10s"')

    def test_key_delivered_status_shows_results_link_and_keeps_polling(self):
        """
        key_delivered ainda não é terminal (TASK-052) — o ack do gateway pode
        chegar num heartbeat seguinte e levar a sessão a `ready`, então o
        polling continua; o usuário já pode ver resultados parciais enquanto
        isso.
        """
        self.session.status = ReportSessionStatus.KEY_DELIVERED
        self.session.save(update_fields=['status'])

        response = self.client.get(f'/portal/relatorios/{self.session.session_id}/')
        self.assertContains(response, 'Ver resultados')
        self.assertContains(response, 'hx-trigger="every 10s"')

    def test_ready_status_shows_pronto_and_stops_polling(self):
        self.session.status = ReportSessionStatus.READY
        self.session.save(update_fields=['status'])

        response = self.client.get(f'/portal/relatorios/{self.session.session_id}/')
        self.assertContains(response, 'Pronto')
        self.assertContains(response, 'Ver resultados')
        self.assertNotContains(response, 'hx-trigger="every 10s"')

    def test_expired_status_shows_clear_message_and_new_report_link(self):
        self.session.status = ReportSessionStatus.EXPIRED
        self.session.save(update_fields=['status'])

        response = self.client.get(f'/portal/relatorios/{self.session.session_id}/')
        self.assertContains(response, 'expirou')
        self.assertContains(response, 'Gerar novo relatório')

    def test_other_clinic_session_returns_404(self):
        self.client.cookies.clear()
        self.client.post('/portal/login/', {'email': 'gerente@b.com', 'password': 'senha-123'})

        response = self.client.get(f'/portal/relatorios/{self.session.session_id}/')
        self.assertEqual(response.status_code, 404)

    def test_status_fragment_endpoint_returns_only_fragment(self):
        response = self.client.get(f'/portal/relatorios/{self.session.session_id}/status/fragment/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="report-status"')
        self.assertNotContains(response, '<header>')


class ReportResultsViewTest(TestCase):
    def setUp(self):
        self.clinic_a = make_clinic('Clínica A')
        self.clinic_b = make_clinic('Clínica B')
        self.user_a = make_clinic_user(self.clinic_a, email='gerente@a.com')
        make_clinic_user(self.clinic_b, email='gerente@b.com')
        self.client.post('/portal/login/', {'email': 'gerente@a.com', 'password': 'senha-123'})

        self.session = services.create_report_session(
            clinic=self.clinic_a, created_by=self.user_a, entities=['patients', 'appointments'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        self.session.status = ReportSessionStatus.KEY_DELIVERED
        self.session.save(update_fields=['status'])

    @patch('portal_gestor.template_views.report_reads.read_appointments_report')
    @patch('portal_gestor.template_views.report_reads.read_patients_report')
    def test_renders_patients_and_appointments_tables(self, mock_patients, mock_appointments):
        mock_patients.return_value = [{'id': 'p1', 'name': 'Fulano', 'document': '123', 'phone': None,
                                        'email': None, 'metadata': None, 'updated_at': '2026-07-10'}]
        mock_appointments.return_value = []

        response = self.client.get(f'/portal/relatorios/{self.session.session_id}/resultados/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Fulano')
        self.assertContains(response, 'Nenhum agendamento disponível')
        mock_patients.assert_called_once_with(self.clinic_a, self.session)

    @patch('portal_gestor.template_views.report_reads.read_patients_report')
    def test_permission_denied_shows_friendly_message_not_500(self, mock_patients):
        mock_patients.side_effect = PermissionDenied('session_expired')

        response = self.client.get(f'/portal/relatorios/{self.session.session_id}/resultados/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'expirou ou ainda não está pronta')
        self.assertContains(response, 'Gerar novo relatório')

    @patch('portal_gestor.template_views.report_reads.read_patients_report')
    def test_report_unavailable_shows_friendly_message_not_500(self, mock_patients):
        mock_patients.side_effect = report_reads.ReportUnavailable()

        response = self.client.get(f'/portal/relatorios/{self.session.session_id}/resultados/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Não foi possível consultar')

    def test_other_clinic_session_returns_404(self):
        self.client.cookies.clear()
        self.client.post('/portal/login/', {'email': 'gerente@b.com', 'password': 'senha-123'})

        response = self.client.get(f'/portal/relatorios/{self.session.session_id}/resultados/')
        self.assertEqual(response.status_code, 404)

    def test_requires_login(self):
        self.client.cookies.clear()
        response = self.client.get(f'/portal/relatorios/{self.session.session_id}/resultados/')
        self.assertEqual(response.status_code, 302)
