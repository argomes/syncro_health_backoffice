"""
Testes da TASK-043 — leitura de relatórios via TemporaryKey.

Cobre: formato de criptografia bit-compatível com o gateway Go (nonce
prefixado, GCM sem AAD, base64 padrão), gate de 403 para sessão
expirada/não-entregue/fora de escopo/sem chave no cache, e o caminho feliz de
decriptação linha a linha (Postgres da clínica mockado — não há instância real
disponível neste ambiente de teste).
"""
import base64
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import MagicMock, patch

import psycopg2
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from clinics.models import Clinic, ClinicStatus, Plan

from . import crypto, report_reads, services
from .models import PortalReadAuditLog, ReportSessionStatus, resync_entities_for_scope


def _encrypt_field(plaintext: bytes, key: bytes) -> str:
    """Réplica em Python do formato do gateway Go: nonce(12) || ct+tag, base64 padrão."""
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    return base64.b64encode(nonce + ct).decode()


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


class CryptoFormatTest(TestCase):
    """Prova o formato bit-a-bit descrito em crypto.py: nonce primeiro, GCM sem AAD,
    base64 padrão — sem depender de um vetor gerado pelo Go (não disponível
    neste ambiente), via round-trip determinístico e checagem de estrutura."""

    def test_round_trip(self):
        key = os.urandom(32)
        plaintext = b'12345678900'
        ciphertext_b64 = _encrypt_field(plaintext, key)

        self.assertEqual(crypto.decrypt_field(ciphertext_b64, key), plaintext)

    def test_nonce_is_prefix_not_suffix(self):
        """Se decodificarmos os 12 primeiros bytes como nonce e o resto como
        ct+tag, o round-trip funciona; se a implementação tratasse os ÚLTIMOS
        12 bytes como nonce (erro comum), o teste abaixo pegaria isso."""
        key = os.urandom(32)
        plaintext = b'campo de teste'
        nonce = os.urandom(12)
        ct = AESGCM(key).encrypt(nonce, plaintext, None)
        blob_b64 = base64.b64encode(nonce + ct).decode()

        self.assertEqual(crypto.decrypt_field(blob_b64, key), plaintext)

    def test_wrong_key_raises_decryption_error(self):
        key = os.urandom(32)
        wrong_key = os.urandom(32)
        ciphertext_b64 = _encrypt_field(b'segredo', key)

        with self.assertRaises(crypto.DecryptionError):
            crypto.decrypt_field(ciphertext_b64, wrong_key)

    def test_empty_ciphertext_returns_empty_bytes(self):
        """Espelha o Go: clinical_notes_enc vazio quando o campo original era vazio — não é erro."""
        self.assertEqual(crypto.decrypt_field('', os.urandom(32)), b'')
        self.assertEqual(crypto.decrypt_field(None, os.urandom(32)), b'')

    def test_key_wrong_size_raises(self):
        with self.assertRaises(crypto.DecryptionError):
            crypto.decrypt_field(_encrypt_field(b'x', os.urandom(32)), os.urandom(16))

    def test_decrypt_dek_wraps_field_and_validates_size(self):
        temp_key = os.urandom(32)
        dek = os.urandom(32)
        dek_encrypted = _encrypt_field(dek, temp_key)

        self.assertEqual(crypto.decrypt_dek(dek_encrypted, temp_key), dek)

    def test_decrypt_dek_wrong_size_raises(self):
        temp_key = os.urandom(32)
        short_dek_encrypted = _encrypt_field(b'too-short', temp_key)

        with self.assertRaises(crypto.DecryptionError):
            crypto.decrypt_dek(short_dek_encrypted, temp_key)

    def test_session_key_id_is_deterministic_16_hex_chars(self):
        import hashlib
        temp_key = os.urandom(32)
        expected = hashlib.sha256(temp_key).hexdigest()[:16]

        self.assertEqual(crypto.session_key_id(temp_key), expected)
        self.assertEqual(len(crypto.session_key_id(temp_key)), 16)
        # Determinístico — mesma chave sempre produz o mesmo id.
        self.assertEqual(crypto.session_key_id(temp_key), crypto.session_key_id(temp_key))

    def test_temp_key_of_one_clinic_cannot_decrypt_dek_of_another_clinic(self):
        """TASK-044 cenário (f) — as DEKs são por-registro e por-clínica: um
        dek_encrypted_session gravado pelo gateway da clínica B (cifrado com a
        TemporaryKey da sessão de B) nunca deve ser decriptável com a
        TemporaryKey de uma sessão da clínica A, mesmo que ambas as sessões
        estejam ativas ao mesmo tempo e mesmo se a TemporaryKey de A vazasse
        (log, erro). Testa a suposição explicitamente — não assume que é óbvio."""
        temp_key_clinic_a = os.urandom(32)
        temp_key_clinic_b = os.urandom(32)
        self.assertNotEqual(temp_key_clinic_a, temp_key_clinic_b)

        dek_of_clinic_b_record = os.urandom(32)
        dek_encrypted_session_b = _encrypt_field(dek_of_clinic_b_record, temp_key_clinic_b)

        # A "TemporaryKey de A vazou" — um atacante (ou bug de escopo) tenta
        # usá-la para abrir um registro que pertence à clínica B.
        with self.assertRaises(crypto.DecryptionError):
            crypto.decrypt_dek(dek_encrypted_session_b, temp_key_clinic_a)


class ReportReadsAccessControlTest(TestCase):
    """Testa os gates de 403 em report_reads, sem tocar em Postgres real."""

    def setUp(self):
        cache.clear()
        self.clinic = make_clinic()

    def _make_session(self, status_=ReportSessionStatus.KEY_DELIVERED, expires_delta=timedelta(hours=1),
                       entities=None, put_key_in_cache=True):
        session = services.create_report_session(
            clinic=self.clinic, created_by=None, entities=entities or ['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        session.status = status_
        session.expires_at = timezone.now() + expires_delta
        session.save(update_fields=['status', 'expires_at'])
        if not put_key_in_cache:
            cache.delete(services._cache_key(session.session_id))
        return session

    def test_entity_not_in_scope_rejected(self):
        session = self._make_session(entities=['patients'])
        with self.assertRaises(PermissionDenied):
            report_reads.read_appointments_report(self.clinic, session)

    def test_professionals_entity_not_in_scope_rejected(self):
        session = self._make_session(entities=['patients'])
        with self.assertRaises(PermissionDenied):
            report_reads.read_professionals_report(self.clinic, session)

    def test_professionals_expired_session_rejected(self):
        session = self._make_session(entities=['professionals'], expires_delta=timedelta(seconds=-1))
        with self.assertRaises(PermissionDenied):
            report_reads.read_professionals_report(self.clinic, session)

    def test_professionals_missing_key_in_cache_rejected(self):
        session = self._make_session(entities=['professionals'], put_key_in_cache=False)
        with self.assertRaises(PermissionDenied):
            report_reads.read_professionals_report(self.clinic, session)

    def test_medical_records_entity_not_in_scope_rejected(self):
        session = self._make_session(entities=['patients'])
        with self.assertRaises(PermissionDenied):
            report_reads.read_medical_records_report(self.clinic, session)

    def test_medical_records_expired_session_rejected(self):
        session = self._make_session(entities=['medical_records'], expires_delta=timedelta(seconds=-1))
        with self.assertRaises(PermissionDenied):
            report_reads.read_medical_records_report(self.clinic, session)

    def test_medical_records_missing_key_in_cache_rejected(self):
        session = self._make_session(entities=['medical_records'], put_key_in_cache=False)
        with self.assertRaises(PermissionDenied):
            report_reads.read_medical_records_report(self.clinic, session)

    def test_expired_session_rejected(self):
        session = self._make_session(expires_delta=timedelta(seconds=-1))
        with self.assertRaises(PermissionDenied):
            report_reads.read_patients_report(self.clinic, session)

    def test_pending_status_rejected(self):
        """PENDING = ainda não entregue ao gateway — nada pra ler ainda."""
        session = self._make_session(status_=ReportSessionStatus.PENDING)
        with self.assertRaises(PermissionDenied):
            report_reads.read_patients_report(self.clinic, session)

    def test_missing_key_in_cache_rejected(self):
        session = self._make_session(put_key_in_cache=False)
        with self.assertRaises(PermissionDenied):
            report_reads.read_patients_report(self.clinic, session)

    def test_expired_status_rejected(self):
        session = self._make_session(status_=ReportSessionStatus.EXPIRED)
        with self.assertRaises(PermissionDenied):
            report_reads.read_patients_report(self.clinic, session)


class ReportReadsHappyPathTest(TestCase):
    """Caminho feliz com o Postgres da clínica mockado — simula linhas já
    cifradas exatamente como o gateway as gravaria."""

    def setUp(self):
        cache.clear()
        self.clinic = make_clinic()
        self.session = services.create_report_session(
            clinic=self.clinic, created_by=None,
            entities=['patients', 'appointments', 'professionals', 'medical_records'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        self.session.status = ReportSessionStatus.KEY_DELIVERED
        self.session.save(update_fields=['status'])

        cached = cache.get(services._cache_key(self.session.session_id))
        self.temp_key = base64.b64decode(cached)
        self.dek = os.urandom(32)
        self.dek_encrypted_session = _encrypt_field(self.dek, self.temp_key)

    def _mock_connection(self, rows):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = rows
        mock_cursor.__enter__.return_value = mock_cursor
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__.return_value = mock_conn
        return mock_conn

    @patch('portal_gestor.report_reads.clinic_db_connection')
    def test_decrypts_patient_row_correctly(self, mock_conn_ctx):
        pid = uuid.uuid4()
        row = (
            pid, 'Fulano de Tal',
            _encrypt_field(b'12345678900', self.dek),
            _encrypt_field(b'11999999999', self.dek),
            _encrypt_field(b'fulano@x.com', self.dek),
            _encrypt_field(b'{"vip": true}', self.dek),
            self.dek_encrypted_session,
            timezone.now(),
        )
        mock_conn_ctx.return_value = self._mock_connection([row])

        results = report_reads.read_patients_report(self.clinic, self.session)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], str(pid))
        self.assertEqual(results[0]['document'], '12345678900')
        self.assertEqual(results[0]['phone'], '11999999999')
        self.assertEqual(results[0]['email'], 'fulano@x.com')
        self.assertEqual(results[0]['metadata'], {'vip': True})

    @patch('portal_gestor.report_reads.clinic_db_connection')
    def test_row_with_corrupted_dek_is_skipped_not_crashed(self, mock_conn_ctx):
        pid = uuid.uuid4()
        row = (pid, 'Fulano', '', '', '', '', 'ciphertext-invalido-nao-decripta==', timezone.now())
        mock_conn_ctx.return_value = self._mock_connection([row])

        results = report_reads.read_patients_report(self.clinic, self.session)
        self.assertEqual(results, [])

    @patch('portal_gestor.report_reads.clinic_db_connection')
    def test_decrypts_appointment_row_correctly(self, mock_conn_ctx):
        aid = uuid.uuid4()
        patient_id = uuid.uuid4()
        row = (
            aid, patient_id, timezone.now(), timezone.now(), 'scheduled', 'obs não-clínica',
            _encrypt_field(b'nota clinica sensivel', self.dek),
            _encrypt_field(b'{"room": "101"}', self.dek),
            self.dek_encrypted_session,
            timezone.now(),
        )
        mock_conn_ctx.return_value = self._mock_connection([row])

        results = report_reads.read_appointments_report(self.clinic, self.session)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['clinical_notes'], 'nota clinica sensivel')
        self.assertEqual(results[0]['notes'], 'obs não-clínica')
        self.assertEqual(results[0]['metadata'], {'room': '101'})

    @patch('portal_gestor.report_reads.clinic_db_connection')
    def test_cross_session_row_is_not_readable_even_if_sql_filter_were_bypassed(self, mock_conn_ctx):
        """
        Cenário: sessão 1 expira, sessão 2 é criada para a mesma clínica. Uma
        linha ainda carrega dek_encrypted_session/session_key_id da sessão 1
        (o gateway só re-sincroniza no próximo heartbeat). O filtro SQL
        `session_key_id = %s` já deveria excluir essa linha da query da sessão
        2 — mas este teste prova a segunda camada de defesa: mesmo que a linha
        chegasse ao Python (bug de query, índice desatualizado, etc.), a DEK
        foi cifrada com a TemporaryKey da sessão 1, então tentar abri-la com a
        TemporaryKey da sessão 2 falha e a linha é descartada, nunca vaza.
        """
        session1_temp_key = os.urandom(32)
        dek = os.urandom(32)
        # Linha como o gateway a gravou durante a sessão 1.
        dek_encrypted_under_session1 = _encrypt_field(dek, session1_temp_key)

        # Sessão 2: TemporaryKey diferente (cache já populado por create_report_session).
        session2_temp_key = base64.b64decode(cache.get(services._cache_key(self.session.session_id)))
        self.assertNotEqual(session1_temp_key, session2_temp_key)

        pid = uuid.uuid4()
        stale_row = (
            pid, 'Fulano', '', '', '', '',
            dek_encrypted_under_session1,  # ainda cifrada sob a sessão 1
            timezone.now(),
        )
        mock_conn_ctx.return_value = self._mock_connection([stale_row])

        results = report_reads.read_patients_report(self.clinic, self.session)

        # A linha "vazou" pelo filtro SQL (mock não filtra de verdade), mas a
        # decriptação da DEK falha porque a chave é de outra sessão — descartada.
        self.assertEqual(results, [])

    def test_session_key_id_differs_across_sessions(self):
        """Duas sessões distintas produzem session_key_id distintos — é essa
        distinção que sustenta o filtro `WHERE session_key_id = %s` e a
        segunda camada de defesa (decrypt_dek falhando) testada acima."""
        temp_key_1 = os.urandom(32)
        temp_key_2 = os.urandom(32)

        self.assertNotEqual(crypto.session_key_id(temp_key_1), crypto.session_key_id(temp_key_2))

    @patch('portal_gestor.report_reads.clinic_db_connection')
    def test_db_operational_error_returns_503_not_crash(self, mock_conn_ctx):
        mock_conn_ctx.side_effect = psycopg2.OperationalError('could not connect')

        with self.assertRaises(report_reads.ReportUnavailable):
            report_reads.read_patients_report(self.clinic, self.session)

    @patch('portal_gestor.report_reads.clinic_db_connection')
    def test_row_encrypted_by_another_clinics_session_is_not_readable(self, mock_conn_ctx):
        """TASK-044 cenário (f), fim-a-fim: mesmo se (por bug de query cross-DB,
        ou dado copiado indevidamente) uma linha cifrada pelo gateway de OUTRA
        clínica chegasse ao Python durante a leitura do relatório da clínica A,
        a TemporaryKey de A nunca decripta a DEK dessa linha — ela foi cifrada
        com a TemporaryKey da sessão da clínica B. A linha é descartada, nunca
        aparece no relatório de A."""
        other_clinic = make_clinic()
        other_session = services.create_report_session(
            clinic=other_clinic, created_by=None, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        other_temp_key = base64.b64decode(cache.get(services._cache_key(other_session.session_id)))
        self.assertNotEqual(other_temp_key, self.temp_key)

        dek_of_other_clinic_record = os.urandom(32)
        dek_encrypted_under_other_clinic = _encrypt_field(dek_of_other_clinic_record, other_temp_key)

        pid = uuid.uuid4()
        foreign_row = (
            pid, 'Nome Vazado?', '', '', '', '',
            dek_encrypted_under_other_clinic,  # cifrada sob a TemporaryKey de OUTRA clínica
            timezone.now(),
        )
        mock_conn_ctx.return_value = self._mock_connection([foreign_row])

        results = report_reads.read_patients_report(self.clinic, self.session)

        self.assertEqual(results, [])

    @patch('portal_gestor.report_reads.clinic_db_connection')
    def test_reads_professional_row_correctly(self, mock_conn_ctx):
        """`professionals` não usa o envelope de criptografia por sessão (ver
        docstring de read_professionals_report) — os dados chegam em texto
        plano do Postgres da clínica, então o teste confere que a leitura
        reflete exatamente o que foi inserido no fixture, sem passar por
        decriptação alguma."""
        prof_id = uuid.uuid4()
        row = (
            prof_id, 'Dra. Fulana de Tal', 'doctor', 'CRM', '123456-SP',
            'active', {'especialidade': 'Cardiologia'}, '225125', timezone.now(),
        )
        mock_conn_ctx.return_value = self._mock_connection([row])

        results = report_reads.read_professionals_report(self.clinic, self.session)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], str(prof_id))
        self.assertEqual(results[0]['name'], 'Dra. Fulana de Tal')
        self.assertEqual(results[0]['role'], 'doctor')
        self.assertEqual(results[0]['registry_type'], 'CRM')
        self.assertEqual(results[0]['registry'], '123456-SP')
        self.assertEqual(results[0]['status'], 'active')
        self.assertEqual(results[0]['metadata'], {'especialidade': 'Cardiologia'})
        self.assertEqual(results[0]['cbo_code'], '225125')

    @patch('portal_gestor.report_reads.clinic_db_connection')
    def test_professionals_empty_result_returns_empty_list_not_error(self, mock_conn_ctx):
        mock_conn_ctx.return_value = self._mock_connection([])

        results = report_reads.read_professionals_report(self.clinic, self.session)

        self.assertEqual(results, [])

    @patch('portal_gestor.report_reads.clinic_db_connection')
    def test_reads_medical_record_row_correctly(self, mock_conn_ctx):
        """`medical_records` (TASK-BO-19) também não usa envelope de
        criptografia — a tabela remota só tem metadados de referência (o SOAP
        clínico nunca sai do SQLite local do gateway, ver EDGW-078). Confere
        que a leitura reflete exatamente o fixture, sem decriptação."""
        record_id = uuid.uuid4()
        appointment_id = uuid.uuid4()
        patient_id = uuid.uuid4()
        professional_id = uuid.uuid4()
        row = (record_id, appointment_id, patient_id, professional_id, True, 3, timezone.now())
        mock_conn_ctx.return_value = self._mock_connection([row])

        results = report_reads.read_medical_records_report(self.clinic, self.session)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], str(record_id))
        self.assertEqual(results[0]['appointment_id'], str(appointment_id))
        self.assertEqual(results[0]['patient_id'], str(patient_id))
        self.assertEqual(results[0]['professional_id'], str(professional_id))
        self.assertEqual(results[0]['finalized'], True)
        self.assertEqual(results[0]['version'], 3)
        self.assertNotIn('queixa', results[0])
        self.assertNotIn('conduta', results[0])

    @patch('portal_gestor.report_reads.clinic_db_connection')
    def test_medical_records_empty_result_returns_empty_list_not_error(self, mock_conn_ctx):
        mock_conn_ctx.return_value = self._mock_connection([])

        results = report_reads.read_medical_records_report(self.clinic, self.session)

        self.assertEqual(results, [])


# =========================================================================
# TASK-BO-R02/R03 — faturamento (convênio × particular)
# =========================================================================

# Subconjunto do schema real do Postgres da clínica (clinics/sql/clinic_schema/
# 001_initial_schema.sql, 004_dek_session_columns.sql e
# 011_appointment_payments.sql) — só as colunas que as queries de faturamento
# tocam. Executa o SQL REAL de report_reads (GROUP BY, JOINs, filtros, CAST em
# centavos) num SQLite em memória, porque não há Postgres neste ambiente de
# teste (sem containers — mesma regra do CI). Os fragmentos usados nas queries
# foram escritos em SQL portável justamente para permitir isso.
_BILLING_TEST_SCHEMA = """
    CREATE TABLE professionals (id TEXT PRIMARY KEY, name TEXT NOT NULL, deleted_at TIMESTAMPTZ);
    CREATE TABLE insurance_operators (id TEXT PRIMARY KEY, name TEXT NOT NULL, ans_code TEXT NOT NULL,
                                      deleted_at TIMESTAMPTZ);
    CREATE TABLE appointments (
        id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, professional_id TEXT NOT NULL,
        start_time TIMESTAMPTZ NOT NULL, status TEXT NOT NULL,
        metadata_enc TEXT NOT NULL DEFAULT '', dek_encrypted_session TEXT, session_key_id TEXT,
        tiss_codigo_procedimento TEXT, tiss_valor_procedimento NUMERIC(10, 2),
        deleted_at TIMESTAMPTZ
    );
    CREATE TABLE appointment_payments (
        id TEXT PRIMARY KEY, appointment_id TEXT NOT NULL, patient_id TEXT NOT NULL,
        amount NUMERIC(10, 2) NOT NULL, method TEXT NOT NULL, created_by TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL, deleted_at TIMESTAMPTZ
    );
"""

sqlite3.register_converter('TIMESTAMPTZ', lambda raw: datetime.fromisoformat(raw.decode()))


def _sqlite_param(value):
    """Adapta parâmetros como o psycopg2 faria: UUID/datetime viram texto
    comparável (todas as datas do teste são UTC, então ISO ordena certo)."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(dt_timezone.utc).isoformat()
    return value


class _SQLiteCursor:
    def __init__(self, conn):
        self._cur = conn.cursor()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._cur.close()
        return False

    def execute(self, query, params=()):
        self._cur.execute(query.replace('%s', '?'), [_sqlite_param(p) for p in params])

    def fetchall(self):
        return self._cur.fetchall()


class _SQLiteConnection:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return _SQLiteCursor(self._conn)


class FakeClinicDB:
    """Substituto de `clinic_db_connection` apoiado em SQLite em memória."""

    def __init__(self):
        self.conn = sqlite3.connect(':memory:', detect_types=sqlite3.PARSE_DECLTYPES)
        self.conn.executescript(_BILLING_TEST_SCHEMA)

    @contextmanager
    def __call__(self, clinic):
        yield _SQLiteConnection(self.conn)

    def insert(self, table, **columns):
        names = ', '.join(columns)
        marks = ', '.join('?' for _ in columns)
        self.conn.execute(
            f'INSERT INTO {table} ({names}) VALUES ({marks})',
            [_sqlite_param(v) for v in columns.values()],
        )


def _make_billing_session(clinic, entities=('billing',), status_=ReportSessionStatus.KEY_DELIVERED,
                          expires_delta=timedelta(hours=1), put_key_in_cache=True):
    session = services.create_report_session(
        clinic=clinic, created_by=None, entities=list(entities),
        date_from=datetime(2026, 9, 1, 0, 0, tzinfo=dt_timezone.utc),
        date_to=datetime(2026, 9, 30, 23, 59, 59, tzinfo=dt_timezone.utc),
    )
    session.status = status_
    session.expires_at = timezone.now() + expires_delta
    session.save(update_fields=['status', 'expires_at'])
    if not put_key_in_cache:
        cache.delete(services._cache_key(session.session_id))
    return session


class BillingReportAccessControlTest(TestCase):
    """Gate 403 de faturamento — idêntico ao de read_appointments_report, e o
    banco da clínica nunca é sequer aberto quando o gate barra."""

    def setUp(self):
        cache.clear()
        self.clinic = make_clinic()

    def _assert_forbidden_without_db(self, session):
        for read_fn in (report_reads.read_billing_report, report_reads.read_billing_summary):
            with self.subTest(read_fn=read_fn.__name__), \
                    patch('portal_gestor.report_reads.clinic_db_connection') as mock_conn:
                with self.assertRaises(PermissionDenied):
                    read_fn(self.clinic, session)
                mock_conn.assert_not_called()

    def test_billing_not_in_scope_rejected(self):
        self._assert_forbidden_without_db(_make_billing_session(self.clinic, entities=['appointments']))

    def test_expired_session_rejected(self):
        self._assert_forbidden_without_db(
            _make_billing_session(self.clinic, expires_delta=timedelta(seconds=-1)))

    def test_missing_key_in_cache_rejected(self):
        self._assert_forbidden_without_db(_make_billing_session(self.clinic, put_key_in_cache=False))

    def test_pending_session_rejected(self):
        self._assert_forbidden_without_db(
            _make_billing_session(self.clinic, status_=ReportSessionStatus.PENDING))

    def test_expired_status_rejected(self):
        self._assert_forbidden_without_db(
            _make_billing_session(self.clinic, status_=ReportSessionStatus.EXPIRED))

    @patch('portal_gestor.report_reads.clinic_db_connection')
    def test_db_operational_error_becomes_report_unavailable(self, mock_conn_ctx):
        mock_conn_ctx.side_effect = psycopg2.OperationalError('could not connect')
        session = _make_billing_session(self.clinic)

        for read_fn in (report_reads.read_billing_report, report_reads.read_billing_summary):
            with self.subTest(read_fn=read_fn.__name__):
                with self.assertRaises(report_reads.ReportUnavailable):
                    read_fn(self.clinic, session)


class BillingReportAggregationTest(TestCase):
    """Agregação real (SQL executado) sobre uma clínica com mistura de
    particular + convênio, 2 profissionais e 2 operadoras, mais os casos que
    NÃO podem entrar na soma (cancelado, fora do período, pagamento estornado)."""

    def setUp(self):
        cache.clear()
        self.clinic = make_clinic()
        self.session = _make_billing_session(self.clinic)
        self.temp_key = base64.b64decode(cache.get(services._cache_key(self.session.session_id)))
        self.key_id = crypto.session_key_id(self.temp_key)

        self.db = FakeClinicDB()
        patcher = patch('portal_gestor.report_reads.clinic_db_connection', self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.ana = str(uuid.uuid4())
        self.bruno = str(uuid.uuid4())
        self.unimed = str(uuid.uuid4())
        self.bradesco = str(uuid.uuid4())
        self.in_window = datetime(2026, 9, 15, 14, 0, tzinfo=dt_timezone.utc)

    def _seed_reference_data(self):
        self.db.insert('professionals', id=self.ana, name='Dra. Ana')
        self.db.insert('professionals', id=self.bruno, name='Dr. Bruno')
        self.db.insert('insurance_operators', id=self.unimed, name='Unimed', ans_code='111111')
        self.db.insert('insurance_operators', id=self.bradesco, name='Bradesco Saúde', ans_code='222222')

    def _envelope(self, metadata: dict, temp_key=None):
        """Cifra o metadata como o gateway (AppointmentSyncStrategy) faria."""
        dek = os.urandom(32)
        key = temp_key or self.temp_key
        return {
            'metadata_enc': _encrypt_field(json.dumps(metadata).encode(), dek),
            'dek_encrypted_session': _encrypt_field(dek, key),
            'session_key_id': crypto.session_key_id(key),
        }

    def _appointment(self, professional_id, *, status='completed', tiss_valor=None, start_time=None,
                     operator_id=None, envelope=None):
        appointment_id = str(uuid.uuid4())
        if envelope is None:
            metadata = {'insurance_choice': operator_id} if operator_id else {}
            envelope = self._envelope(metadata)
        self.db.insert(
            'appointments', id=appointment_id, patient_id=str(uuid.uuid4()),
            professional_id=professional_id, start_time=start_time or self.in_window, status=status,
            tiss_codigo_procedimento='10101012' if tiss_valor else None,
            tiss_valor_procedimento=tiss_valor, **envelope,
        )
        return appointment_id

    def _payment(self, appointment_id, amount, *, method='pix', created_at=None, deleted_at=None):
        payment_id = str(uuid.uuid4())
        self.db.insert(
            'appointment_payments', id=payment_id, appointment_id=appointment_id,
            patient_id=str(uuid.uuid4()), amount=amount, method=method, created_by='recep',
            created_at=created_at or self.in_window, deleted_at=deleted_at,
        )
        return payment_id

    def _seed_mixed_clinic(self):
        self._seed_reference_data()
        # Convênio que conta.
        self._appointment(self.ana, tiss_valor=150.10, operator_id=self.unimed)
        self._appointment(self.ana, tiss_valor=200.00, operator_id=self.bradesco)
        self._appointment(self.bruno, tiss_valor=99.90, operator_id=self.unimed.upper())  # caixa normalizada
        # Convênio cifrado sob a chave de OUTRA sessão — conta no total, mas a
        # operadora não pode ser lida: vai para "não identificada".
        self._appointment(self.bruno, tiss_valor=80.00,
                          envelope=self._envelope({'insurance_choice': self.unimed}, temp_key=os.urandom(32)))
        # Convênio que NÃO conta: cancelado e fora do período.
        self._appointment(self.bruno, status='cancelled', tiss_valor=500.00, operator_id=self.unimed)
        self._appointment(self.ana, tiss_valor=1000.00, operator_id=self.unimed,
                          start_time=datetime(2026, 8, 31, 23, 0, tzinfo=dt_timezone.utc))
        # Particular que conta.
        ana_particular = self._appointment(self.ana, operator_id=None)
        self._payment(ana_particular, 300.00, method='pix')
        bruno_particular = self._appointment(self.bruno, operator_id=None)
        self._payment(bruno_particular, 120.50, method='dinheiro')
        # Pagamento cujo atendimento ainda não sincronizou (sem FK na nuvem).
        self._payment(str(uuid.uuid4()), 50.00, method='cartao')
        # Particular que NÃO conta: estornado (soft delete) e fora do período.
        self._payment(bruno_particular, 999.00, deleted_at=self.in_window)
        self._payment(ana_particular, 777.00, created_at=datetime(2026, 10, 1, 0, 0, 1, tzinfo=dt_timezone.utc))

    def test_empty_clinic_returns_zeros_without_error(self):
        summary = report_reads.read_billing_summary(self.clinic, self.session)

        self.assertEqual(summary['total_geral']['total_cents'], 0)
        self.assertEqual(summary['total_geral']['count'], 0)
        self.assertEqual(summary['convenio']['total_cents'], 0)
        self.assertEqual(summary['particular']['total_cents'], 0)
        self.assertEqual(summary['by_professional'], [])
        self.assertEqual(summary['by_operator'], [])
        self.assertEqual(report_reads.read_billing_report(self.clinic, self.session), [])

    def test_summary_aggregates_mixed_particular_convenio_professionals_operators(self):
        self._seed_mixed_clinic()

        summary = report_reads.read_billing_summary(self.clinic, self.session)

        self.assertEqual(summary['convenio'], {
            'label': 'Faturado (convênio)', 'total_cents': 15010 + 20000 + 9990 + 8000, 'count': 4,
        })
        self.assertEqual(summary['particular'], {
            'label': 'Particular lançado', 'total_cents': 30000 + 12050 + 5000, 'count': 3,
        })
        self.assertEqual(summary['total_geral']['total_cents'], 53000 + 47050)
        self.assertEqual(summary['total_geral']['count'], 7)

        by_prof = {p['professional_id']: p for p in summary['by_professional']}
        self.assertEqual(by_prof[self.ana], {
            'professional_id': self.ana, 'professional_name': 'Dra. Ana',
            'convenio_cents': 35010, 'convenio_count': 2,
            'particular_cents': 30000, 'particular_count': 1, 'total_cents': 65010,
        })
        self.assertEqual(by_prof[self.bruno], {
            'professional_id': self.bruno, 'professional_name': 'Dr. Bruno',
            'convenio_cents': 17990, 'convenio_count': 2,
            'particular_cents': 12050, 'particular_count': 1, 'total_cents': 30040,
        })
        self.assertEqual(by_prof[None]['particular_cents'], 5000)
        self.assertEqual([p['professional_id'] for p in summary['by_professional']], [self.ana, self.bruno, None])

        by_op = {o['operator_id']: o for o in summary['by_operator']}
        self.assertEqual(by_op[self.unimed.lower()], {
            'operator_id': self.unimed.lower(), 'operator_name': 'Unimed', 'ans_code': '111111',
            'total_cents': 25000, 'count': 2,
        })
        self.assertEqual(by_op[self.bradesco.lower()]['total_cents'], 20000)
        self.assertEqual(by_op[None]['operator_name'], 'Operadora não identificada')
        self.assertEqual(by_op[None]['total_cents'], 8000)
        # Invariante: a quebra por operadora fecha com o total de convênio.
        self.assertEqual(sum(o['total_cents'] for o in summary['by_operator']), summary['convenio']['total_cents'])

        for amount in (summary['total_geral']['total_cents'], *(p['total_cents'] for p in summary['by_professional'])):
            self.assertIs(type(amount), int)

    def test_summary_has_no_phi_and_never_says_recebido(self):
        self._seed_mixed_clinic()

        dumped = json.dumps(report_reads.read_billing_summary(self.clinic, self.session), ensure_ascii=False)

        self.assertNotIn('patient', dumped)
        self.assertNotIn('recebido', dumped.lower())

    def test_billing_lines_have_no_patient_data_and_carry_operator(self):
        self._seed_mixed_clinic()

        lines = report_reads.read_billing_report(self.clinic, self.session)

        self.assertEqual(len(lines), 7)
        self.assertEqual(sum(line['amount_cents'] for line in lines), 100050)
        for line in lines:
            self.assertNotIn('patient_id', line)
            self.assertIs(type(line['amount_cents']), int)

        convenio = [line for line in lines if line['payment_type'] == 'convenio']
        particular = [line for line in lines if line['payment_type'] == 'particular']
        self.assertEqual(len(convenio), 4)
        self.assertEqual(len(particular), 3)
        self.assertEqual(
            sorted((line['operator_name'] or '-') for line in convenio),
            ['-', 'Bradesco Saúde', 'Unimed', 'Unimed'],
        )
        self.assertEqual(sorted(line['method'] for line in particular), ['cartao', 'dinheiro', 'pix'])
        self.assertTrue(all(line['procedure_code'] == '10101012' for line in convenio))

    def test_particular_choice_or_garbage_metadata_never_becomes_an_operator(self):
        self._seed_reference_data()
        self._appointment(self.ana, tiss_valor=10.00, envelope=self._envelope({'insurance_choice': 'particular'}))
        self._appointment(self.ana, tiss_valor=20.00, envelope={
            'metadata_enc': 'nao-e-base64-valido', 'dek_encrypted_session': 'lixo', 'session_key_id': self.key_id,
        })

        summary = report_reads.read_billing_summary(self.clinic, self.session)

        self.assertEqual(len(summary['by_operator']), 1)
        self.assertIsNone(summary['by_operator'][0]['operator_id'])
        self.assertEqual(summary['by_operator'][0]['total_cents'], 3000)


class BillingResyncExpansionTest(TestCase):
    """`billing` é escopo de autorização, não entidade do gateway — precisa
    virar `appointments` no resync_window, senão a operadora nunca é decriptável."""

    def test_billing_scope_expands_to_appointments_without_duplicates(self):
        self.assertEqual(resync_entities_for_scope(['billing']), ['appointments'])
        self.assertEqual(resync_entities_for_scope(['appointments', 'billing']), ['appointments'])
        self.assertEqual(resync_entities_for_scope(['patients', 'billing']), ['patients', 'appointments'])
        self.assertEqual(resync_entities_for_scope(['patients']), ['patients'])

    def test_heartbeat_payload_sends_gateway_entities_not_billing(self):
        clinic = make_clinic()
        clinic.public_key_pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key() \
            .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        clinic.save(update_fields=['public_key_pem'])
        services.create_report_session(
            clinic=clinic, created_by=None, entities=['billing'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )

        payload = services.get_pending_session_key_payload(clinic)

        self.assertEqual(payload['resync_window']['entities'], ['appointments'])
