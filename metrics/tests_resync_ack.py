"""
Testes da TASK-052 — resync_ack no heartbeat: o gateway confirma que a janela de
resync sob demanda (TASK-041) foi de fato persistida, e a ReportSession
(TASK-042/043) sai de key_delivered/syncing e chega a `ready`.
"""
import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import ClinicUser
from clinics.models import Clinic, ClinicStatus, Plan
from portal_gestor import services
from portal_gestor.models import ReportSessionStatus


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


def make_clinic_user(clinic, email='gerente@a.com'):
    user = ClinicUser(clinic=clinic, email=email, name='Gerente')
    user.set_password('senha-123')
    user.save()
    return user


class ResyncAckTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.clinic = make_clinic()
        self.user = make_clinic_user(self.clinic)

    def _heartbeat(self, resync_ack=None):
        payload = {'gateway_version': '1.0.0'}
        if resync_ack is not None:
            payload['resync_ack'] = resync_ack
        return self.client.post(
            '/api/metrics/heartbeat', payload, format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )

    def _make_session(self, status_):
        session = services.create_report_session(
            clinic=self.clinic, created_by=self.user, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        session.status = status_
        session.save(update_fields=['status'])
        return session

    def test_ack_moves_key_delivered_session_to_ready(self):
        session = self._make_session(ReportSessionStatus.KEY_DELIVERED)

        response = self._heartbeat(resync_ack={'session_id': str(session.session_id)})

        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.status, ReportSessionStatus.READY)

    def test_ack_moves_syncing_session_to_ready(self):
        session = self._make_session(ReportSessionStatus.SYNCING)

        self._heartbeat(resync_ack={'session_id': str(session.session_id)})

        session.refresh_from_db()
        self.assertEqual(session.status, ReportSessionStatus.READY)

    def test_ack_without_matching_session_does_not_crash(self):
        response = self._heartbeat(resync_ack={'session_id': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 200)

    def test_ack_for_pending_session_ignored(self):
        """pending significa que a chave nem foi entregue ainda — um ack não
        deveria conseguir pular direto pra ready sem passar por key_delivered."""
        session = self._make_session(ReportSessionStatus.PENDING)

        self._heartbeat(resync_ack={'session_id': str(session.session_id)})

        session.refresh_from_db()
        self.assertEqual(session.status, ReportSessionStatus.PENDING)

    def test_ack_for_already_expired_session_does_not_reopen_it(self):
        session = self._make_session(ReportSessionStatus.EXPIRED)

        self._heartbeat(resync_ack={'session_id': str(session.session_id)})

        session.refresh_from_db()
        self.assertEqual(session.status, ReportSessionStatus.EXPIRED)

    def test_ack_for_session_past_expires_at_does_not_reopen_it(self):
        """Mesmo se o status no banco ainda for key_delivered (worker de limpeza
        não rodou), um ack não deve reviver uma sessão cujo expires_at já passou."""
        session = self._make_session(ReportSessionStatus.KEY_DELIVERED)
        session.expires_at = timezone.now() - timedelta(seconds=1)
        session.save(update_fields=['expires_at'])

        self._heartbeat(resync_ack={'session_id': str(session.session_id)})

        session.refresh_from_db()
        self.assertNotEqual(session.status, ReportSessionStatus.READY)

    def test_ack_for_already_ready_session_is_idempotent(self):
        session = self._make_session(ReportSessionStatus.READY)

        response = self._heartbeat(resync_ack={'session_id': str(session.session_id)})

        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.status, ReportSessionStatus.READY)

    def test_ack_scoped_to_own_clinic_cannot_affect_other_clinic_session(self):
        other_clinic = make_clinic('Outra Clínica')
        other_user = make_clinic_user(other_clinic, email='outro@b.com')
        other_session = services.create_report_session(
            clinic=other_clinic, created_by=other_user, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        other_session.status = ReportSessionStatus.KEY_DELIVERED
        other_session.save(update_fields=['status'])

        # Heartbeat autenticado como self.clinic tentando confirmar uma sessão de other_clinic.
        self._heartbeat(resync_ack={'session_id': str(other_session.session_id)})

        other_session.refresh_from_db()
        self.assertEqual(other_session.status, ReportSessionStatus.KEY_DELIVERED)

    def test_heartbeat_without_resync_ack_field_works_normally(self):
        response = self._heartbeat()
        self.assertEqual(response.status_code, 200)

    def test_malformed_resync_ack_does_not_crash(self):
        response = self._heartbeat(resync_ack={'session_id': 'not-a-uuid'})
        self.assertEqual(response.status_code, 200)

        response2 = self._heartbeat(resync_ack='garbage-not-a-dict')
        self.assertIn(response2.status_code, (200, 400))
