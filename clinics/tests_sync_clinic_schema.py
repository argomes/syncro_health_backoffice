"""
TASK-BO-R01 — testes do comando `sync_clinic_schema`, que fecha o gap de
clínicas já provisionadas nunca receberem uma migration nova de
clinic_schema/ (ver docstring do comando para o contexto completo).
"""
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase

from clinics.models import Clinic


class SyncClinicSchemaCommandTests(TestCase):
    def setUp(self):
        # cnpj é unique=True (blank=True só permite UM registro em branco por
        # Postgres tratar '' como valor igual, não NULL) — cada clínica de
        # teste precisa de um cnpj distinto pra não colidir entre si.
        self.clinic = Clinic.objects.create(
            name='Clínica Teste',
            slug='clinica-teste',
            cnpj='11111111000111',
            db_name='db_teste123',
            db_user='user_teste123',
        )
        # clínica sem provisionamento concluído (sem db_name/db_user) —
        # nunca deve ser candidata, não há banco pra conectar.
        Clinic.objects.create(name='Clínica Sem Provisão', slug='sem-provisao', cnpj='22222222000122')

    @patch('clinics.provisioning.psycopg2.connect')
    def test_applies_schema_only_to_provisioned_clinics(self, mock_connect):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        out = StringIO()
        call_command('sync_clinic_schema', stdout=out)

        # só a clínica com db_name/db_user preenchidos gera uma conexão
        self.assertEqual(mock_connect.call_count, 1)
        self.assertIn('OK  — clinica-teste', out.getvalue())
        self.assertNotIn('sem-provisao', out.getvalue())

    @patch('clinics.provisioning.psycopg2.connect')
    def test_clinic_slug_filter_limits_to_one_clinic(self, mock_connect):
        Clinic.objects.create(
            name='Outra Clínica',
            slug='outra-clinica',
            cnpj='33333333000133',
            db_name='db_outra456',
            db_user='user_outra456',
        )
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = MagicMock()

        out = StringIO()
        call_command('sync_clinic_schema', '--clinic-slug=clinica-teste', stdout=out)

        self.assertEqual(mock_connect.call_count, 1)
        mock_connect.assert_called_with(mock_connect.call_args.args[0] if mock_connect.call_args.args else None, dbname='db_teste123')

    @patch('clinics.provisioning.psycopg2.connect')
    def test_one_clinic_failure_does_not_abort_the_others(self, mock_connect):
        other = Clinic.objects.create(
            name='Outra Clínica',
            slug='outra-clinica',
            cnpj='33333333000133',
            db_name='db_outra456',
            db_user='user_outra456',
        )

        ok_conn = MagicMock()
        ok_conn.cursor.return_value = MagicMock()

        def connect_side_effect(dsn, dbname):
            if dbname == self.clinic.db_name:
                raise Exception('Connection refused')
            return ok_conn

        mock_connect.side_effect = connect_side_effect

        out = StringIO()
        call_command('sync_clinic_schema', stdout=out)

        self.assertEqual(mock_connect.call_count, 2)
        self.assertIn('FALHOU — clinica-teste', out.getvalue())
        self.assertIn(f'OK  — {other.slug}', out.getvalue())
        self.assertIn('1 ok, 1 falharam, 2 clínicas', out.getvalue())

    def test_no_provisioned_clinics_warns_and_exits_cleanly(self):
        Clinic.objects.all().delete()
        out = StringIO()
        call_command('sync_clinic_schema', stdout=out)
        self.assertIn('Nenhuma clínica provisionada', out.getvalue())
