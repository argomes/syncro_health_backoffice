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
from .models import ReportSessionStatus


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
