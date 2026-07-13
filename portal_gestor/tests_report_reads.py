"""
Testes da TASK-043 — leitura de relatórios via TemporaryKey.

Cobre: formato de criptografia bit-compatível com o gateway Go (nonce
prefixado, GCM sem AAD, base64 padrão), gate de 403 para sessão
expirada/não-entregue/fora de escopo/sem chave no cache, e o caminho feliz de
decriptação linha a linha (Postgres da clínica mockado — não há instância real
disponível neste ambiente de teste).
"""
import base64
import os
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import psycopg2
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from clinics.models import Clinic, ClinicStatus, Plan

from . import crypto, report_reads, services
from .models import ReportSessionStatus


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
            clinic=self.clinic, created_by=None, entities=['patients', 'appointments'],
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
