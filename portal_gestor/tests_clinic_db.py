"""
Testes de BACFF-AVULSA-06 — redesenho de clinic_db_connection().

Cobre: PermissionDenied explícito sem grant vigente no cache, e conexão
bem-sucedida (psycopg2.connect mockado — sem Postgres real disponível neste
ambiente de teste) quando o grant está presente, usando o db_user escopado
da clínica em vez do superuser de provisionamento.
"""
import uuid
from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from clinics.models import Clinic, ClinicStatus, Plan

from .clinic_db import clinic_db_connection


def make_clinic():
    return Clinic.objects.create(
        name='Clínica Teste',
        slug=f'clinica-{uuid.uuid4().hex[:8]}',
        plan=Plan.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
        db_name=f'clinic_{uuid.uuid4().hex[:8]}',
        db_user=f'u_{uuid.uuid4().hex[:8]}',
    )


class ClinicDbConnectionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.clinic = make_clinic()

    def test_raises_permission_denied_without_grant(self):
        with self.assertRaises(PermissionDenied):
            with clinic_db_connection(self.clinic):
                pass

    def test_raises_permission_denied_when_grant_expired_from_cache(self):
        # Simula expiração real do Redis: chave simplesmente não está mais lá.
        cache.delete(f"clinic_db_grant:{self.clinic.id}")
        with self.assertRaises(PermissionDenied):
            with clinic_db_connection(self.clinic):
                pass

    @patch('portal_gestor.clinic_db.psycopg2.connect')
    def test_connects_with_scoped_user_and_grant_password_when_grant_present(self, mock_connect):
        cache.set(f"clinic_db_grant:{self.clinic.id}", {
            'password': 's3nh4-temp',
            'granted_by': 'admin-local',
            'granted_at': '2026-07-17T10:00:00',
            'expires_at': '2026-07-17T14:00:00',
        }, timeout=14400)

        with clinic_db_connection(self.clinic) as conn:
            self.assertIs(conn, mock_connect.return_value)

        mock_connect.assert_called_once()
        dsn = mock_connect.call_args[0][0]
        self.assertIn(self.clinic.db_user, dsn)
        self.assertIn('s3nh4-temp', dsn)
        self.assertIn(self.clinic.db_name, dsn)
        conn = mock_connect.return_value
        conn.set_session.assert_called_once_with(readonly=True, autocommit=True)
        conn.close.assert_called_once()

    def test_does_not_reference_provisioning_database_url_setting(self):
        """Garante que o módulo não LÊ settings.PROVISIONING_DATABASE_URL —
        núcleo do requisito de BACFF-AVULSA-06 (superuser só em provisioning.py).
        O nome ainda aparece no docstring explicando a decisão, então checamos
        apenas o padrão de acesso real (`settings.PROVISIONING_DATABASE_URL`)."""
        import portal_gestor.clinic_db as clinic_db_module
        import inspect

        source = inspect.getsource(clinic_db_module)
        self.assertNotIn('settings.PROVISIONING_DATABASE_URL', source)
