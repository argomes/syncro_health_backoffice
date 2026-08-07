"""
Testes de wiring HTTP dos endpoints de leitura de relatório (TASK-043) —
auth, isolamento entre clínicas, e propagação de PermissionDenied como 403.
A lógica de decriptação/acesso já é coberta em tests_report_reads.py; aqui só
garantimos que a view conecta tudo certo.
"""
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from accounts.models import ClinicUser
from clinics.models import Clinic, ClinicStatus, Plan

from . import services
from .models import PortalReadAuditLog, ReportSessionStatus


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


class ReportReadViewsTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.clinic_a = make_clinic('Clínica A')
        self.clinic_b = make_clinic('Clínica B')
        self.user_a = make_clinic_user(self.clinic_a, email='gerente@a.com')
        self.user_b = make_clinic_user(self.clinic_b, email='gerente@b.com')

        self.session = services.create_report_session(
            clinic=self.clinic_a, created_by=self.user_a, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        self.session.status = ReportSessionStatus.KEY_DELIVERED
        self.session.save(update_fields=['status'])

    def _login(self, email, password='senha-123'):
        resp = self.client.post('/portal/api/auth/login/', {'email': email, 'password': password}, format='json')
        return {'HTTP_AUTHORIZATION': f'Bearer {resp.data["access"]}'}

    @patch('portal_gestor.report_reads.read_patients_report')
    def test_patients_report_requires_auth(self, mock_read):
        response = self.client.get(f'/portal/api/reports/sessions/{self.session.session_id}/patients/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        mock_read.assert_not_called()

    @patch('portal_gestor.report_reads.read_patients_report')
    def test_patients_report_happy_path(self, mock_read):
        mock_read.return_value = [{'id': 'x', 'name': 'Fulano'}]
        auth = self._login('gerente@a.com')

        response = self.client.get(f'/portal/api/reports/sessions/{self.session.session_id}/patients/', **auth)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['name'], 'Fulano')

    @patch('portal_gestor.report_reads.read_patients_report')
    def test_other_clinic_gets_404_not_leak_of_existence(self, mock_read):
        auth_b = self._login('gerente@b.com')
        response = self.client.get(f'/portal/api/reports/sessions/{self.session.session_id}/patients/', **auth_b)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_read.assert_not_called()

    @patch('portal_gestor.report_reads.read_appointments_report')
    def test_appointments_other_clinic_gets_404_not_leak_of_existence(self, mock_read):
        """TASK-044 cenário (b) — a mesma garantia de isolamento validada acima
        para /patients/ precisa valer também para /appointments/ (o outro path
        que efetivamente devolve PHI decriptado). Um ClinicUser da clínica B
        nunca deve conseguir nem confirmar que a sessão de A existe (404, não
        403), e report_reads nunca deve ser chamado para o registro de outra
        clínica."""
        auth_b = self._login('gerente@b.com')
        response = self.client.get(f'/portal/api/reports/sessions/{self.session.session_id}/appointments/', **auth_b)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_read.assert_not_called()

    @patch('portal_gestor.report_reads.read_patients_report')
    def test_permission_denied_from_service_becomes_403(self, mock_read):
        mock_read.side_effect = PermissionDenied('session_expired')
        auth = self._login('gerente@a.com')

        response = self.client.get(f'/portal/api/reports/sessions/{self.session.session_id}/patients/', **auth)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch('portal_gestor.report_reads.read_appointments_report')
    def test_appointments_report_wired_correctly(self, mock_read):
        mock_read.return_value = []
        auth = self._login('gerente@a.com')

        response = self.client.get(f'/portal/api/reports/sessions/{self.session.session_id}/appointments/', **auth)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_read.assert_called_once_with(self.clinic_a, self.session)

    def test_nonexistent_session_returns_404(self):
        auth = self._login('gerente@a.com')
        response = self.client.get(f'/portal/api/reports/sessions/{uuid.uuid4()}/patients/', **auth)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ProfessionalsReportViewTest(TestCase):
    """BACFF-AVULSA-05 — mesmo padrão anti-IDOR das views de patients/appointments,
    aplicado à nova ProfessionalsReportView."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.clinic_a = make_clinic('Clínica A')
        self.clinic_b = make_clinic('Clínica B')
        self.user_a = make_clinic_user(self.clinic_a, email='gerente@a.com')
        self.user_b = make_clinic_user(self.clinic_b, email='gerente@b.com')

        self.session = services.create_report_session(
            clinic=self.clinic_a, created_by=self.user_a, entities=['professionals'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        self.session.status = ReportSessionStatus.KEY_DELIVERED
        self.session.save(update_fields=['status'])

    def _login(self, email, password='senha-123'):
        resp = self.client.post('/portal/api/auth/login/', {'email': email, 'password': password}, format='json')
        return {'HTTP_AUTHORIZATION': f'Bearer {resp.data["access"]}'}

    @patch('portal_gestor.report_reads.read_professionals_report')
    def test_professionals_report_happy_path(self, mock_read):
        mock_read.return_value = [{'id': 'x', 'name': 'Dr. Fulano'}]
        auth = self._login('gerente@a.com')

        response = self.client.get(f'/portal/api/reports/sessions/{self.session.session_id}/professionals/', **auth)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        mock_read.assert_called_once_with(self.clinic_a, self.session)

    @patch('portal_gestor.report_reads.read_professionals_report')
    def test_professionals_other_clinic_gets_404_not_leak_of_existence(self, mock_read):
        auth_b = self._login('gerente@b.com')
        response = self.client.get(f'/portal/api/reports/sessions/{self.session.session_id}/professionals/', **auth_b)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_read.assert_not_called()
        self.assertEqual(PortalReadAuditLog.objects.count(), 0)

    @patch('portal_gestor.report_reads.read_medical_records_report')
    def test_medical_records_report_happy_path(self, mock_read):
        mock_read.return_value = [{'id': 'mr-1', 'patient_id': 'p-1', 'finalized': True}]
        auth = self._login('gerente@a.com')

        response = self.client.get(
            f'/portal/api/reports/sessions/{self.session.session_id}/medical-records/', **auth
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        mock_read.assert_called_once_with(self.clinic_a, self.session)

    @patch('portal_gestor.report_reads.read_medical_records_report')
    def test_medical_records_other_clinic_gets_404_not_leak_of_existence(self, mock_read):
        """TASK-BO-19 — teste de isolamento explícito: a clínica B nunca
        consegue ler o metadado de prontuário de um paciente da clínica A,
        mesmo autenticada e mesmo sabendo o session_id (IDOR clássico) —
        `_ReportReadView.get` resolve a sessão sempre escopada ao
        `request.user.clinic`, então uma sessão de outra clínica simplesmente
        não existe do ponto de vista da query, e o read_fn nem chega a rodar."""
        auth_b = self._login('gerente@b.com')

        response = self.client.get(
            f'/portal/api/reports/sessions/{self.session.session_id}/medical-records/', **auth_b
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_read.assert_not_called()
        self.assertEqual(PortalReadAuditLog.objects.count(), 0)


class PortalReadAuditLogTest(TestCase):
    """
    BACFF-AVULSA-05 (LGPD Art. 37) — cobre a gravação do audit log de leitura,
    fatorada em `_ReportReadView.get`. Testa as 3 views (patients, appointments,
    professionals) para garantir que a fatoração cobre todas, não só uma.
    """

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.clinic_a = make_clinic('Clínica A')
        self.user_a = make_clinic_user(self.clinic_a, email='gerente@a.com')

        self.session = services.create_report_session(
            clinic=self.clinic_a, created_by=self.user_a,
            entities=['patients', 'appointments', 'professionals', 'medical_records'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        self.session.status = ReportSessionStatus.KEY_DELIVERED
        self.session.save(update_fields=['status'])

    def _login(self, email='gerente@a.com', password='senha-123'):
        resp = self.client.post('/portal/api/auth/login/', {'email': email, 'password': password}, format='json')
        return {'HTTP_AUTHORIZATION': f'Bearer {resp.data["access"]}'}

    @patch('portal_gestor.report_reads.read_patients_report')
    def test_audit_log_written_on_successful_read_with_results(self, mock_read):
        mock_read.return_value = [
            {'id': 'p1', 'name': 'Fulano de Tal', 'document': '12345678900', 'email': 'fulano@x.com'},
            {'id': 'p2', 'name': 'Beltrano', 'document': '98765432100', 'email': 'beltrano@x.com'},
        ]
        auth = self._login()

        response = self.client.get(f'/portal/api/reports/sessions/{self.session.session_id}/patients/', **auth)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(PortalReadAuditLog.objects.count(), 1)
        log = PortalReadAuditLog.objects.get()
        self.assertEqual(log.clinic, self.clinic_a)
        self.assertEqual(log.clinic_user, self.user_a)
        self.assertEqual(log.session_id, self.session.session_id)
        self.assertEqual(log.entity, 'patients')
        self.assertEqual(log.record_count, 2)

    @patch('portal_gestor.report_reads.read_appointments_report')
    def test_audit_log_written_even_when_result_is_empty(self, mock_read):
        """Acceptance criteria explícito da task: 0 resultados ainda é uma
        operação de tratamento que precisa ficar registrada."""
        mock_read.return_value = []
        auth = self._login()

        response = self.client.get(f'/portal/api/reports/sessions/{self.session.session_id}/appointments/', **auth)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(PortalReadAuditLog.objects.count(), 1)
        log = PortalReadAuditLog.objects.get()
        self.assertEqual(log.entity, 'appointments')
        self.assertEqual(log.record_count, 0)

    @patch('portal_gestor.report_reads.read_professionals_report')
    def test_audit_log_written_for_professionals_view(self, mock_read):
        mock_read.return_value = [{'id': 'x'}]
        auth = self._login()

        response = self.client.get(f'/portal/api/reports/sessions/{self.session.session_id}/professionals/', **auth)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        log = PortalReadAuditLog.objects.get()
        self.assertEqual(log.entity, 'professionals')
        self.assertEqual(log.record_count, 1)

    @patch('portal_gestor.report_reads.read_medical_records_report')
    def test_audit_log_written_for_medical_records_view(self, mock_read):
        mock_read.return_value = [{'id': 'mr-1'}]
        auth = self._login()

        response = self.client.get(
            f'/portal/api/reports/sessions/{self.session.session_id}/medical-records/', **auth
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        log = PortalReadAuditLog.objects.get()
        self.assertEqual(log.entity, 'medical_records')
        self.assertEqual(log.record_count, 1)

    @patch('portal_gestor.report_reads.read_patients_report')
    def test_no_audit_log_written_when_read_denied(self, mock_read):
        """Só leitura BEM-SUCEDIDA gera registro — PermissionDenied não é uma
        operação de tratamento realizada, então não deve gerar log."""
        mock_read.side_effect = PermissionDenied('session_expired')
        auth = self._login()

        response = self.client.get(f'/portal/api/reports/sessions/{self.session.session_id}/patients/', **auth)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(PortalReadAuditLog.objects.count(), 0)

    @patch('portal_gestor.report_reads.read_patients_report')
    def test_audit_log_contains_no_phi(self, mock_read):
        """
        Prova ativamente que o audit log não carrega PHI — não basta 'não
        adicionei o campo'. Duas verificações independentes:
        1) o model só expõe os campos de metadados esperados (uma coluna nova
           adicionada displicentemente no futuro quebraria este teste);
        2) nenhum valor de PHI conhecido do fixture (nome, documento, email)
           aparece em nenhum valor serializado do registro persistido.
        """
        phi_name = 'Paciente Sigiloso da Silva'
        phi_document = '11122233344'
        phi_email = 'paciente.sigiloso@example.com'
        mock_read.return_value = [
            {'id': 'p1', 'name': phi_name, 'document': phi_document, 'email': phi_email,
             'metadata': {'nota': 'informação clínica sensível'}},
        ]
        auth = self._login()

        self.client.get(f'/portal/api/reports/sessions/{self.session.session_id}/patients/', **auth)

        log = PortalReadAuditLog.objects.get()

        expected_fields = {'id', 'clinic_user', 'clinic', 'session_id', 'entity', 'record_count', 'created_at'}
        actual_field_names = {f.name for f in PortalReadAuditLog._meta.fields}
        self.assertEqual(actual_field_names, expected_fields)

        serialized_values = ' '.join(str(getattr(log, f.name)) for f in PortalReadAuditLog._meta.fields)
        self.assertNotIn(phi_name, serialized_values)
        self.assertNotIn(phi_document, serialized_values)
        self.assertNotIn(phi_email, serialized_values)
        self.assertNotIn('informação clínica sensível', serialized_values)
