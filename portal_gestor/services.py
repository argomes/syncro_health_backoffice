import base64
import logging
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.core.cache import cache
from django.db import connection, transaction
from django.utils import timezone

from .models import DEFAULT_REPORT_SESSION_TTL_HOURS, ReportSession, ReportSessionStatus

logger = logging.getLogger(__name__)

TEMP_KEY_BYTES = 32  # AES-256, mesmo tamanho de chave usado no lado gateway (Go)
_CACHE_KEY_PREFIX = 'report_session_temp_key'


def _cache_key(session_id) -> str:
    return f'{_CACHE_KEY_PREFIX}:{session_id}'


def _encrypt_bytes_with_public_key(public_key_pem: str, plaintext: bytes) -> str:
    """
    Variante de clinics.provisioning.encrypt_with_public_key para dados
    binários arbitrários (a TemporaryKey é uma chave AES-256 crua, não um
    texto). A versão original força `.encode()` UTF-8 no plaintext — corromperia
    bytes >= 0x80. O lado gateway (Go) faz `crypto.DecryptRSAOAEP(...)` e usa o
    resultado diretamente como `[]byte` da chave (TASK-040/041, health_worker.go)
    — nenhuma camada extra de base64 no conteúdo decriptado, então aqui também
    não deve haver uma.
    """
    public_key = serialization.load_pem_public_key(public_key_pem.encode())
    ciphertext = public_key.encrypt(
        plaintext,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(ciphertext).decode()


def create_report_session(clinic, created_by, entities, date_from, date_to, ttl_hours=None) -> ReportSession:
    """
    Cria uma ReportSession e gera a TemporaryKey correspondente, guardando-a
    SÓ no cache (nunca em disco) com TTL = expires_at.

    Retorna imediatamente — não bloqueia esperando a entrega ao gateway, que
    só acontece no próximo heartbeat (ver services.get_pending_session_key_payload
    e metrics/views.py::heartbeat). Ver nota "UX é obrigatoriamente assíncrona"
    na task doc TASK-042.
    """
    ttl_hours = ttl_hours or DEFAULT_REPORT_SESSION_TTL_HOURS
    expires_at = timezone.now() + timezone.timedelta(hours=ttl_hours)

    session = ReportSession.objects.create(
        clinic=clinic,
        created_by=created_by,
        entities_scope=list(entities),
        date_from=date_from,
        date_to=date_to,
        status=ReportSessionStatus.PENDING,
        expires_at=expires_at,
    )

    temp_key = os.urandom(TEMP_KEY_BYTES)
    ttl_seconds = max(int((expires_at - timezone.now()).total_seconds()), 1)
    cache.set(_cache_key(session.session_id), base64.b64encode(temp_key).decode(), timeout=ttl_seconds)

    return session


def get_pending_session_key_payload(clinic) -> dict | None:
    """
    Chamada pelo heartbeat handler (metrics/views.py). Se houver uma
    ReportSession pendente e não expirada para esta clínica, decripta a
    TemporaryKey do cache, re-cifra com a chave pública RSA da clínica (mesmo
    esquema de clinics/provisioning.py::encrypt_with_public_key, usado para
    DBPasswordEncrypted) e retorna o payload pronto para o heartbeat response:

        {
            "pending_session_key": {"session_id", "temp_key_encrypted", "expires_at"},
            "resync_window": {"from", "to", "entities"},
        }

    Nunca loga a TemporaryKey em texto claro — só session_id/timestamps.
    Marca a sessão como KEY_DELIVERED após montar o payload (a confirmação
    definitiva de entrega/sync ainda depende de um endpoint de ack, TODO
    registrado na task doc — fora do escopo desta implementação).
    """
    # select_for_update evita que dois heartbeats concorrentes da mesma clínica
    # (ex.: requests duplicadas, retry do gateway) leiam a mesma sessão PENDING
    # simultaneamente e a entreguem/marquem duas vezes de forma intercalada.
    # skip_locked faz o segundo request simplesmente não achar nada pendente
    # em vez de bloquear esperando o primeiro terminar. SQLite (dev local) não
    # suporta select_for_update — cai para uma leitura sem lock nesse caso;
    # produção usa Postgres (DATABASE_URL), que suporta.
    with transaction.atomic():
        qs = ReportSession.objects.filter(
            clinic=clinic, status=ReportSessionStatus.PENDING, expires_at__gt=timezone.now()
        )
        if connection.features.has_select_for_update:
            qs = qs.select_for_update(
                skip_locked=connection.features.has_select_for_update_skip_locked
            )
        session = qs.order_by('created_at').first()
        if session is None:
            return None

        if not clinic.public_key_pem:
            logger.warning('report_session_missing_public_key clinic_id=%s session_id=%s', clinic.id, session.session_id)
            return None

        cached = cache.get(_cache_key(session.session_id))
        if cached is None:
            # TTL expirou no cache antes do expires_at da sessão (ex.: Redis sob pressão
            # de memória) — não há como recuperar a TemporaryKey. Marca a sessão como
            # expirada em vez de deixar pending indefinidamente sem nunca ser entregue.
            logger.warning('report_session_temp_key_evicted clinic_id=%s session_id=%s', clinic.id, session.session_id)
            session.mark_expired()
            return None

        temp_key = base64.b64decode(cached)
        temp_key_encrypted = _encrypt_bytes_with_public_key(clinic.public_key_pem, temp_key)
        session.mark_key_delivered()

    logger.info('report_session_key_delivered clinic_id=%s session_id=%s', clinic.id, session.session_id)

    return {
        'pending_session_key': {
            'session_id': str(session.session_id),
            'temp_key_encrypted': temp_key_encrypted,
            'expires_at': session.expires_at.isoformat(),
        },
        'resync_window': {
            'from': session.date_from.isoformat(),
            'to': session.date_to.isoformat(),
            'entities': session.entities_scope,
        },
    }
