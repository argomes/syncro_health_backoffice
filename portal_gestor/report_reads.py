"""
TASK-043 — leitura de relatórios via TemporaryKey.

Consulta o Postgres exclusivo da clínica diretamente (portal_gestor.clinic_db),
decripta sob demanda com a TemporaryKey da ReportSession ativa (nunca persiste
dado decriptado), e nunca cai em fallback silencioso para dado cru fora da
janela/sessão autorizada — qualquer situação não coberta levanta
rest_framework.exceptions.PermissionDenied (403).
"""
import base64
import json
import logging

import psycopg2
from django.core.cache import cache
from rest_framework.exceptions import APIException, PermissionDenied

from . import crypto
from .clinic_db import clinic_db_connection
from .models import ReportSessionStatus
from .services import _cache_key

logger = logging.getLogger(__name__)

# Status em que já existe uma TemporaryKey entregue e potencialmente aplicada
# pelo gateway. READY não é alcançável ainda nesta implementação (depende do
# endpoint de ack, TODO explícito da TASK-042) — incluído aqui para quando
# existir, sem exigir mudança neste módulo.
READABLE_STATUSES = {
    ReportSessionStatus.KEY_DELIVERED,
    ReportSessionStatus.SYNCING,
    ReportSessionStatus.READY,
}


class ReportUnavailable(APIException):
    status_code = 503
    default_detail = 'Não foi possível consultar os dados da clínica no momento.'
    default_code = 'report_unavailable'


def _get_temp_key_or_403(session) -> bytes:
    if session.is_expired():
        raise PermissionDenied('session_expired')
    if session.status not in READABLE_STATUSES:
        raise PermissionDenied('session_not_ready')

    cached = cache.get(_cache_key(session.session_id))
    if cached is None:
        raise PermissionDenied('session_key_unavailable')

    return base64.b64decode(cached)


def _require_entity_in_scope(session, entity: str):
    if entity not in session.entities_scope:
        raise PermissionDenied('entity_not_in_session_scope')


def _decrypt_optional_str(value_enc, dek: bytes):
    """Decripta um campo opcional. Nunca propaga o motivo exato de uma falha de
    decriptação ao cliente (poderia vazar informação sobre a chave/dado) — loga
    sem PHI e trata como campo ausente."""
    if not value_enc:
        return None
    try:
        return crypto.decrypt_field_str(value_enc, dek)
    except crypto.DecryptionError:
        logger.warning('report_field_decrypt_failed')
        return None


def _decrypt_optional_json(value_enc, dek: bytes):
    raw = _decrypt_optional_str(value_enc, dek)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


_PATIENT_QUERY = """
    SELECT id, name, document_enc, phone_enc, email_enc, metadata_enc,
           dek_encrypted_session, updated_at
    FROM patients
    WHERE updated_at BETWEEN %s AND %s
      AND session_key_id = %s
      AND deleted_at IS NULL
"""

_APPOINTMENT_QUERY = """
    SELECT id, patient_id, start_time, end_time, status, notes,
           clinical_notes_enc, metadata_enc, dek_encrypted_session, updated_at
    FROM appointments
    WHERE updated_at BETWEEN %s AND %s
      AND session_key_id = %s
      AND deleted_at IS NULL
"""


_PROFESSIONAL_QUERY = """
    SELECT id, name, role, registry_type, registry, status, metadata, cbo_code, updated_at
    FROM professionals
    WHERE updated_at BETWEEN %s AND %s
      AND deleted_at IS NULL
"""


def _run_query(clinic, query, params):
    try:
        with clinic_db_connection(clinic) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return cur.fetchall()
    except (psycopg2.OperationalError, psycopg2.ProgrammingError) as exc:
        logger.error('report_query_failed clinic_id=%s error_type=%s', clinic.id, type(exc).__name__)
        raise ReportUnavailable() from exc


def read_patients_report(clinic, session) -> list[dict]:
    _require_entity_in_scope(session, 'patients')
    temp_key = _get_temp_key_or_403(session)
    key_id = crypto.session_key_id(temp_key)

    rows = _run_query(clinic, _PATIENT_QUERY, (session.date_from, session.date_to, key_id))

    results = []
    for (pid, name, document_enc, phone_enc, email_enc, metadata_enc,
         dek_encrypted_session, updated_at) in rows:
        try:
            dek = crypto.decrypt_dek(dek_encrypted_session, temp_key)
        except crypto.DecryptionError:
            logger.warning('report_dek_decrypt_failed clinic_id=%s', clinic.id)
            continue
        results.append({
            'id': str(pid),
            'name': name,
            'document': _decrypt_optional_str(document_enc, dek),
            'phone': _decrypt_optional_str(phone_enc, dek),
            'email': _decrypt_optional_str(email_enc, dek),
            'metadata': _decrypt_optional_json(metadata_enc, dek),
            'updated_at': updated_at.isoformat() if updated_at else None,
        })
    return results


def read_appointments_report(clinic, session) -> list[dict]:
    _require_entity_in_scope(session, 'appointments')
    temp_key = _get_temp_key_or_403(session)
    key_id = crypto.session_key_id(temp_key)

    rows = _run_query(clinic, _APPOINTMENT_QUERY, (session.date_from, session.date_to, key_id))

    results = []
    for (aid, patient_id, start_time, end_time, appt_status, notes,
         clinical_notes_enc, metadata_enc, dek_encrypted_session, updated_at) in rows:
        try:
            dek = crypto.decrypt_dek(dek_encrypted_session, temp_key)
        except crypto.DecryptionError:
            logger.warning('report_dek_decrypt_failed clinic_id=%s', clinic.id)
            continue
        results.append({
            'id': str(aid),
            'patient_id': str(patient_id),
            'start_time': start_time.isoformat() if start_time else None,
            'end_time': end_time.isoformat() if end_time else None,
            'status': appt_status,
            'notes': notes,
            'clinical_notes': _decrypt_optional_str(clinical_notes_enc, dek),
            'metadata': _decrypt_optional_json(metadata_enc, dek),
            'updated_at': updated_at.isoformat() if updated_at else None,
        })
    return results


def read_professionals_report(clinic, session) -> list[dict]:
    """
    Diferente de `read_patients_report`/`read_appointments_report`: a tabela
    `professionals` no Postgres da clínica NÃO usa o envelope de criptografia
    por sessão (sem `*_enc`, sem `dek_encrypted_session`, sem `session_key_id`
    — confirmado lendo o schema real sincronizado por `ProfessionalSyncStrategy`
    em syncro_gateway/internal/adapters/output/cloud/professional_strategy.go e
    a migration 001_initial_schema.sql do gateway). `name` e `registry` (o
    número de conselho profissional, ex. CRM/CRO) chegam em texto plano no
    banco da clínica hoje.
    # [SECURITY]: gap real, fora do escopo desta rodada (ver BACFF-AVULSA-05)
    # — o ideal seria `professionals` seguir o mesmo dual-envelope de
    # `patients`/`appointments`. Registrado como pendência de produto, não
    # implementado aqui para não expandir escopo sem pedido explícito.
    #
    # Ainda assim, mantemos o MESMO gate de autorização das outras entidades
    # (escopo da sessão + TemporaryKey vigente no cache) — a leitura só é
    # permitida dentro da janela de uma ReportSession autorizada e não
    # expirada, mesmo que não haja DEK para decriptar aqui.
    """
    _require_entity_in_scope(session, 'professionals')
    _get_temp_key_or_403(session)  # só o gate de autorização — sem uso de DEK abaixo.

    rows = _run_query(clinic, _PROFESSIONAL_QUERY, (session.date_from, session.date_to))

    results = []
    for (prof_id, name, role, registry_type, registry, prof_status, metadata,
         cbo_code, updated_at) in rows:
        results.append({
            'id': str(prof_id),
            'name': name,
            'role': role,
            'registry_type': registry_type,
            'registry': registry,
            'status': prof_status,
            'metadata': metadata,
            'cbo_code': cbo_code,
            'updated_at': updated_at.isoformat() if updated_at else None,
        })
    return results
