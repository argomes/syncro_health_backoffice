"""
Testes da TASK-042 — emissão de ReportSession + entrega via heartbeat.

Cobre: criação de sessão (auth ClinicUser), TemporaryKey nunca persistida em
disco, entrega via heartbeat (pending_session_key + resync_window), formato
cross-language da chave (RSA decripta para os 32 bytes crus, sem camada extra
de base64 — é isso que o gateway Go espera), expiração, e isolamento entre
clínicas.
"""
import base64
import os
import subprocess
import sys
import uuid
from datetime import timedelta

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from accounts.models import ClinicUser
from clinics.models import Clinic, ClinicStatus, Plan

from . import services
from .models import ReportSession, ReportSessionStatus


def _generate_rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def _rsa_decrypt_bytes(ciphertext_b64: str, private_key_pem: str) -> bytes:
    """Simula o que o gateway Go faz (crypto.DecryptRSAOAEP) — decripta para bytes crus."""
    private_key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    ciphertext = base64.b64decode(ciphertext_b64)
    return private_key.decrypt(
        ciphertext,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )


def make_clinic(name='Clínica Teste', with_rsa=True):
    public_pem = ''
    if with_rsa:
        _, public_pem = _generate_rsa_keypair()
    return Clinic.objects.create(
        name=name,
        slug=f'clinica-{uuid.uuid4().hex[:8]}',
        plan=Plan.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
        db_name=f'clinic_{uuid.uuid4().hex[:8]}',
        db_user=f'u_{uuid.uuid4().hex[:8]}',
        public_key_pem=public_pem,
    )


def make_clinic_user(clinic, email='gerente@a.com', password='senha-123'):
    user = ClinicUser(clinic=clinic, email=email, name='Gerente')
    user.set_password(password)
    user.save()
    return user


class ReportSessionServiceTest(TestCase):
    """Testes diretos de portal_gestor.services, sem passar pela API HTTP."""

    def setUp(self):
        cache.clear()
        self.private_key_pem, public_pem = _generate_rsa_keypair()
        self.clinic = make_clinic(with_rsa=False)
        self.clinic.public_key_pem = public_pem
        self.clinic.save(update_fields=['public_key_pem'])
        self.user = make_clinic_user(self.clinic)

    def test_create_report_session_defaults(self):
        session = services.create_report_session(
            clinic=self.clinic,
            created_by=self.user,
            entities=['patients'],
            date_from=timezone.now() - timedelta(days=1),
            date_to=timezone.now(),
        )
        self.assertEqual(session.status, ReportSessionStatus.PENDING)
        self.assertEqual(session.entities_scope, ['patients'])
        self.assertAlmostEqual(
            (session.expires_at - timezone.now()).total_seconds(),
            timedelta(hours=2).total_seconds(),
            delta=5,
        )

    def test_temp_key_never_persisted_in_db(self):
        """A TemporaryKey não deve existir em nenhum campo do model — só no cache."""
        services.create_report_session(
            clinic=self.clinic, created_by=self.user, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        field_names = {f.name for f in ReportSession._meta.get_fields()}
        self.assertNotIn('temp_key', field_names)
        self.assertNotIn('temp_key_encrypted', field_names)
        self.assertNotIn('temp_key_encrypted_at_rest', field_names)

    def test_pending_session_key_payload_decrypts_to_raw_32_bytes(self):
        """
        Vetor cross-language: o RSA-OAEP decripta para exatamente os 32 bytes
        crus da chave AES — sem base64 extra — porque é isso que
        health_worker.go (TASK-040) usa diretamente como []byte da TemporaryKey.
        """
        session = services.create_report_session(
            clinic=self.clinic, created_by=self.user, entities=['patients', 'appointments'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )

        payload = services.get_pending_session_key_payload(self.clinic)
        self.assertIsNotNone(payload)

        temp_key_encrypted = payload['pending_session_key']['temp_key_encrypted']
        decrypted = _rsa_decrypt_bytes(temp_key_encrypted, self.private_key_pem)
        self.assertEqual(len(decrypted), services.TEMP_KEY_BYTES)

        session.refresh_from_db()
        self.assertEqual(session.status, ReportSessionStatus.KEY_DELIVERED)
        self.assertIsNotNone(session.delivered_at)

    def test_pending_session_key_payload_includes_resync_window(self):
        date_from = timezone.now() - timedelta(days=3)
        date_to = timezone.now()
        services.create_report_session(
            clinic=self.clinic, created_by=self.user, entities=['patients'],
            date_from=date_from, date_to=date_to,
        )

        payload = services.get_pending_session_key_payload(self.clinic)
        self.assertEqual(payload['resync_window']['entities'], ['patients'])
        self.assertEqual(payload['resync_window']['from'], date_from.isoformat())
        self.assertEqual(payload['resync_window']['to'], date_to.isoformat())

    def test_no_pending_session_returns_none(self):
        self.assertIsNone(services.get_pending_session_key_payload(self.clinic))

    def test_expired_session_not_delivered(self):
        session = services.create_report_session(
            clinic=self.clinic, created_by=self.user, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        session.expires_at = timezone.now() - timedelta(seconds=1)
        session.save(update_fields=['expires_at'])

        self.assertIsNone(services.get_pending_session_key_payload(self.clinic))

    def test_missing_public_key_skips_delivery_without_crash(self):
        self.clinic.public_key_pem = ''
        self.clinic.save(update_fields=['public_key_pem'])

        services.create_report_session(
            clinic=self.clinic, created_by=self.user, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        self.assertIsNone(services.get_pending_session_key_payload(self.clinic))

    def test_cache_eviction_marks_session_expired(self):
        """Se a chave sumir do cache antes do expires_at, a sessão é marcada
        expired em vez de ficar pending para sempre."""
        session = services.create_report_session(
            clinic=self.clinic, created_by=self.user, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        cache.clear()  # simula eviction

        payload = services.get_pending_session_key_payload(self.clinic)
        self.assertIsNone(payload)

        session.refresh_from_db()
        self.assertEqual(session.status, ReportSessionStatus.EXPIRED)

    def test_session_not_delivered_twice_on_repeated_calls(self):
        """Depois de KEY_DELIVERED, chamadas subsequentes (ex.: heartbeats
        concorrentes ou retries) não devem re-entregar a mesma sessão."""
        services.create_report_session(
            clinic=self.clinic, created_by=self.user, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        first = services.get_pending_session_key_payload(self.clinic)
        self.assertIsNotNone(first)

        second = services.get_pending_session_key_payload(self.clinic)
        self.assertIsNone(second)

    def test_oldest_pending_session_delivered_first(self):
        older = services.create_report_session(
            clinic=self.clinic, created_by=self.user, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        services.create_report_session(
            clinic=self.clinic, created_by=self.user, entities=['appointments'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )

        payload = services.get_pending_session_key_payload(self.clinic)
        self.assertEqual(payload['pending_session_key']['session_id'], str(older.session_id))


class BinaryEncryptionNecessityTest(TestCase):
    """Prova que a variante binária (_encrypt_bytes_with_public_key) não é
    over-engineering: passar a TemporaryKey crua pela função original
    baseada em str.encode() de fato corrompe/quebra os dados."""

    def test_original_str_based_encrypt_cannot_carry_raw_key_bytes(self):
        from clinics.provisioning import encrypt_with_public_key

        _, public_pem = _generate_rsa_keypair()

        # A função original exige `plaintext: str` e faz `.encode()` — bytes
        # não tem `.encode()`, então nem compila a chamada direta.
        temp_key = os.urandom(32)
        with self.assertRaises(AttributeError):
            encrypt_with_public_key(public_pem, temp_key)

        # A tentativa "óbvia" de contornar isso — decodificar os bytes crus
        # como se fossem texto antes de reusar a função original — corrompe
        # a chave: bytes >= 0x80 não são UTF-8 válido na maioria das tentativas
        # de 32 bytes aleatórios, e mesmo com errors='replace'/'surrogateescape'
        # o round-trip decode→encode não preserva os bytes originais.
        forced_str = temp_key.decode('utf-8', errors='replace')
        roundtripped = forced_str.encode('utf-8')
        self.assertNotEqual(
            roundtripped, temp_key,
            'decode/encode round-trip deveria corromper bytes >=0x80 da chave crua',
        )

    def test_binary_variant_round_trips_exactly(self):
        private_pem, public_pem = _generate_rsa_keypair()
        temp_key = os.urandom(32)

        encrypted = services._encrypt_bytes_with_public_key(public_pem, temp_key)
        decrypted = _rsa_decrypt_bytes(encrypted, private_pem)

        self.assertEqual(decrypted, temp_key)


class CacheBackendProductionGuardTest(TestCase):
    """
    portal_gestor depende de um cache COMPARTILHADO entre workers (Redis) para
    a TemporaryKey ter garantia real de TTL (ver settings.py). LocMemCache
    (default do Django sem CACHES) é por-processo — em produção multi-worker
    isso faz `get_pending_session_key_payload` marcar sessões válidas como
    `expired` só porque o heartbeat caiu num worker diferente do que criou a
    sessão. settings.py deve falhar cedo (RuntimeError no boot) se DEBUG=False
    e CACHE_URL não estiver configurado, em vez de operar silenciosamente
    sobre um cache que quebra essa garantia.

    Roda em subprocesso porque settings já está carregado no processo de
    teste — não dá para re-simular o boot do Django in-process de forma
    confiável.
    """

    def _boot_with_env(self, extra_env):
        env = os.environ.copy()
        env.update(extra_env)
        result = subprocess.run(
            [sys.executable, '-c', 'import django; django.setup()'],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result

    def test_debug_false_without_cache_url_fails_fast(self):
        result = self._boot_with_env({'DEBUG': 'False', 'CACHE_URL': ''})
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn('CACHE_URL', result.stderr)

    def test_debug_false_with_cache_url_boots(self):
        result = self._boot_with_env({'DEBUG': 'False', 'CACHE_URL': 'redis://localhost:6379/1'})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_debug_true_without_cache_url_boots_with_locmem(self):
        result = self._boot_with_env({'DEBUG': 'True', 'CACHE_URL': ''})
        self.assertEqual(result.returncode, 0, result.stderr)


class ReportSessionAPITest(APITestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.clinic_a = make_clinic(name='Clínica A')
        self.clinic_b = make_clinic(name='Clínica B')
        self.user_a = make_clinic_user(self.clinic_a, email='gerente@a.com')
        self.user_b = make_clinic_user(self.clinic_b, email='gerente@b.com')

    def _login(self, email, password='senha-123'):
        resp = self.client.post('/portal/api/auth/login/', {'email': email, 'password': password}, format='json')
        return resp.data['access']

    def _auth(self, access):
        return {'HTTP_AUTHORIZATION': f'Bearer {access}'}

    def test_create_session_requires_auth(self):
        response = self.client.post('/portal/api/reports/sessions/', {
            'date_from': timezone.now().isoformat(),
            'date_to': (timezone.now() + timedelta(hours=1)).isoformat(),
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_session_returns_immediately_as_pending(self):
        access = self._login('gerente@a.com')
        response = self.client.post('/portal/api/reports/sessions/', {
            'date_from': (timezone.now() - timedelta(days=1)).isoformat(),
            'date_to': timezone.now().isoformat(),
            'entities': ['patients'],
        }, format='json', **self._auth(access))

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], 'pending')
        self.assertIn('session_id', response.data)

    def test_create_session_invalid_date_range_rejected(self):
        access = self._login('gerente@a.com')
        response = self.client.post('/portal/api/reports/sessions/', {
            'date_from': timezone.now().isoformat(),
            'date_to': (timezone.now() - timedelta(days=1)).isoformat(),
        }, format='json', **self._auth(access))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_session_default_entities_when_omitted(self):
        access = self._login('gerente@a.com')
        response = self.client.post('/portal/api/reports/sessions/', {
            'date_from': (timezone.now() - timedelta(days=1)).isoformat(),
            'date_to': timezone.now().isoformat(),
        }, format='json', **self._auth(access))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(set(response.data['entities_scope']), {'patients', 'appointments'})

    def test_detail_endpoint_isolated_between_clinics(self):
        access_a = self._login('gerente@a.com')
        create_resp = self.client.post('/portal/api/reports/sessions/', {
            'date_from': (timezone.now() - timedelta(days=1)).isoformat(),
            'date_to': timezone.now().isoformat(),
        }, format='json', **self._auth(access_a))
        session_id = create_resp.data['session_id']

        access_b = self._login('gerente@b.com')
        response = self.client.get(f'/portal/api/reports/sessions/{session_id}/', **self._auth(access_b))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        response_a = self.client.get(f'/portal/api/reports/sessions/{session_id}/', **self._auth(access_a))
        self.assertEqual(response_a.status_code, status.HTTP_200_OK)

    def test_created_report_session_scoped_to_own_clinic(self):
        access_a = self._login('gerente@a.com')
        self.client.post('/portal/api/reports/sessions/', {
            'date_from': (timezone.now() - timedelta(days=1)).isoformat(),
            'date_to': timezone.now().isoformat(),
        }, format='json', **self._auth(access_a))

        session = ReportSession.objects.get()
        self.assertEqual(session.clinic_id, self.clinic_a.id)
        self.assertEqual(session.created_by_id, self.user_a.id)


class HeartbeatReportSessionIntegrationTest(TestCase):
    """Confirma que o heartbeat handler (metrics/views.py) inclui
    pending_session_key e resync_window quando há uma ReportSession pendente
    para a clínica autenticada via X-License-Key."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.private_key_pem, public_pem = _generate_rsa_keypair()
        self.clinic = make_clinic(with_rsa=False)
        self.clinic.public_key_pem = public_pem
        self.clinic.save(update_fields=['public_key_pem'])
        self.user = make_clinic_user(self.clinic)

    def _heartbeat(self):
        return self.client.post(
            '/api/metrics/heartbeat',
            {'gateway_version': '1.0.0'},
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )

    def test_heartbeat_without_pending_session_has_no_extra_fields(self):
        response = self._heartbeat()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn('pending_session_key', response.data)
        self.assertNotIn('resync_window', response.data)

    def test_heartbeat_with_pending_session_includes_key_and_window(self):
        services.create_report_session(
            clinic=self.clinic, created_by=self.user, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )

        response = self._heartbeat()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('pending_session_key', response.data)
        self.assertIn('resync_window', response.data)

        decrypted = _rsa_decrypt_bytes(
            response.data['pending_session_key']['temp_key_encrypted'], self.private_key_pem
        )
        self.assertEqual(len(decrypted), services.TEMP_KEY_BYTES)

        session = ReportSession.objects.get()
        self.assertEqual(session.status, ReportSessionStatus.KEY_DELIVERED)

    def test_heartbeat_only_delivers_pending_session_of_authenticated_clinic(self):
        """Duas clínicas com sessões pendentes ao mesmo tempo — o heartbeat de
        uma nunca deve incluir a sessão/chave da outra (isolamento fim-a-fim,
        não só no endpoint de detalhe)."""
        other_clinic = make_clinic(name='Outra Clínica')
        other_user = make_clinic_user(other_clinic, email='gerente@outra.com')

        own_session = services.create_report_session(
            clinic=self.clinic, created_by=self.user, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        other_session = services.create_report_session(
            clinic=other_clinic, created_by=other_user, entities=['appointments'],
            date_from=timezone.now() - timedelta(days=2), date_to=timezone.now(),
        )

        response = self._heartbeat()
        self.assertEqual(
            response.data['pending_session_key']['session_id'], str(own_session.session_id)
        )
        self.assertNotEqual(
            response.data['pending_session_key']['session_id'], str(other_session.session_id)
        )

        other_session.refresh_from_db()
        self.assertEqual(other_session.status, ReportSessionStatus.PENDING)

    def test_heartbeat_never_leaks_temp_key_in_plaintext(self):
        """Gera a sessão, chama o heartbeat, e garante que os bytes crus da
        TemporaryKey decriptada nunca aparecem em nenhum campo de texto puro
        da resposta (só o ciphertext base64 deve estar presente)."""
        services.create_report_session(
            clinic=self.clinic, created_by=self.user, entities=['patients'],
            date_from=timezone.now() - timedelta(days=1), date_to=timezone.now(),
        )
        response = self._heartbeat()

        decrypted = _rsa_decrypt_bytes(
            response.data['pending_session_key']['temp_key_encrypted'], self.private_key_pem
        )
        response_text = str(response.data)
        self.assertNotIn(decrypted.hex(), response_text)
        self.assertNotIn(base64.b64encode(decrypted).decode(), response_text)
