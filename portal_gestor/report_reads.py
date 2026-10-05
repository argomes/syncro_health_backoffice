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
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any, TypedDict

import psycopg2
from django.core.cache import cache
from rest_framework.exceptions import APIException, PermissionDenied

from . import crypto
from .clinic_db import clinic_db_connection
from .models import BILLING_REPORT_ENTITY, ReportSessionStatus
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


# TASK-BO-19: `medical_records` só chega ao Postgres da clínica como metadado
# de referência/listagem — id, appointment_id, patient_id, professional_id,
# finalized, version — pushado pelo gateway via MedicalRecordSyncStrategy
# (syncro_gateway/internal/adapters/output/cloud/medical_record_strategy.go,
# EDGW-078). O conteúdo clínico (queixa/evolução/CID/conduta, o SOAP) NUNCA
# sai do SQLite local da clínica — a tabela remota nem tem essas colunas
# (ver migration 006_medical_records_metadata.sql do gateway), então não há
# como este módulo vazar PHI clínico mesmo por engano: o SELECT abaixo só
# pode referenciar o que existe na tabela.
_MEDICAL_RECORD_QUERY = """
    SELECT id, appointment_id, patient_id, professional_id, finalized, version, updated_at
    FROM medical_records
    WHERE updated_at BETWEEN %s AND %s
      AND deleted_at IS NULL
"""


def _run_queries(clinic, statements: Sequence[tuple[str, tuple[Any, ...]]]) -> list[list[tuple[Any, ...]]]:
    """Executa várias consultas numa única conexão ao banco da clínica.

    Os relatórios de faturamento precisam de 3-4 consultas por chamada; abrir
    uma conexão por consulta multiplicaria o handshake TLS/autenticação contra
    o Postgres da clínica sem ganho algum (a conexão já é read-only).
    """
    try:
        with clinic_db_connection(clinic) as conn:
            results: list[list[tuple[Any, ...]]] = []
            for query, params in statements:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    results.append(cur.fetchall())
            return results
    except (psycopg2.OperationalError, psycopg2.ProgrammingError) as exc:
        logger.error('report_query_failed clinic_id=%s error_type=%s', clinic.id, type(exc).__name__)
        raise ReportUnavailable() from exc


def _run_query(clinic, query, params):
    return _run_queries(clinic, [(query, params)])[0]


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


def read_medical_records_report(clinic, session) -> list[dict]:
    """
    TASK-BO-19 — listagem de metadados de prontuário (id/appointment/paciente/
    profissional/finalizado) por clínica. Mesmo gate de autorização das demais
    entidades (sessão precisa estar no escopo, não expirada, com a
    TemporaryKey ainda no cache) — igual a `read_professionals_report`, sem
    uso de DEK porque não há campo cifrado nesta tabela (ela nunca teve PHI
    clínico pra proteger em primeiro lugar).
    """
    _require_entity_in_scope(session, 'medical_records')
    _get_temp_key_or_403(session)  # só o gate de autorização — sem DEK abaixo.

    rows = _run_query(clinic, _MEDICAL_RECORD_QUERY, (session.date_from, session.date_to))

    results = []
    for (record_id, appointment_id, patient_id, professional_id, finalized, version, updated_at) in rows:
        results.append({
            'id': str(record_id),
            'appointment_id': str(appointment_id),
            'patient_id': str(patient_id),
            'professional_id': str(professional_id),
            'finalized': finalized,
            'version': version,
            'updated_at': updated_at.isoformat() if updated_at else None,
        })
    return results


# =========================================================================
# TASK-BO-R02/R03 — faturamento (convênio × particular)
# =========================================================================
#
# Não existe tabela `billing_entries` no Postgres da clínica. O faturamento é
# derivado de duas fontes já sincronizadas pelo gateway:
#
# - Convênio (TISS): `appointments.tiss_valor_procedimento` (NUMERIC(10,2),
#   texto plano — ver clinics/sql/clinic_schema/001_initial_schema.sql) de
#   atendimentos `completed`, atribuído ao `appointments.professional_id`.
#   A operadora NÃO tem coluna própria: a escolha feita no check-in fica em
#   `appointments.metadata.insurance_choice` (operator_id ou "particular"),
#   que só chega à nuvem dentro de `metadata_enc` (envelope por DEK).
# - Particular: `appointment_payments.amount` (NUMERIC(10,2), texto plano —
#   clinics/sql/clinic_schema/011_appointment_payments.sql). A tabela não tem
#   `professional_id`; o profissional vem do JOIN com `appointments`.
#
# Regras de produto (mesmas do resumo mensal do desktop, PR #616):
# - Rótulo do convênio é "Faturado", NUNCA "Recebido": valor de procedimento
#   lançado não é dinheiro em caixa (não há glosa/adjudicação/conciliação
#   bancária sincronizadas para a nuvem).
# - Valores sempre em centavos inteiros, convertidos já no SQL — nenhum float
#   participa da soma (NUMERIC(10,2) * 100 é exato).
# - Período: convênio por `start_time` (data do atendimento, competência);
#   particular por `created_at` (data do lançamento do pagamento) — o mesmo
#   critério de `AppointmentPaymentRepository.SumByMonth` no gateway.

CONVENIO_LABEL = 'Faturado (convênio)'
PARTICULAR_LABEL = 'Particular lançado'
TOTAL_LABEL = 'Total faturado (convênio + particular)'
UNIDENTIFIED_OPERATOR_LABEL = 'Operadora não identificada'
BILLING_SUMMARY_NOTE = (
    'Convênio = valor do procedimento informado em atendimentos concluídos; '
    'não representa valor adjudicado pela operadora nem dinheiro em caixa.'
)

PAYMENT_TYPE_CONVENIO = 'convenio'
PAYMENT_TYPE_PARTICULAR = 'particular'

# Status do atendimento que gera faturamento de convênio. Cancelado/no-show
# podem carregar tiss_valor_procedimento preenchido no agendamento, mas não
# geram guia — contá-los inflaria o faturamento.
_BILLABLE_APPOINTMENT_STATUS = 'completed'

# Fragmentos SQL compartilhados — constantes, nunca interpolam input externo.
# CAST(ROUND(x * 100) AS BIGINT) converte NUMERIC(10,2) em centavos inteiros
# ainda no banco; o CAST externo garante int (SUM de BIGINT no Postgres
# devolve NUMERIC, que o psycopg2 entregaria como Decimal).
_CONVENIO_CENTS = 'CAST(ROUND(a.tiss_valor_procedimento * 100) AS BIGINT)'
_PARTICULAR_CENTS = 'CAST(ROUND(p.amount * 100) AS BIGINT)'

_CONVENIO_FILTER = f"""
    a.deleted_at IS NULL
    AND a.status = '{_BILLABLE_APPOINTMENT_STATUS}'
    AND a.tiss_valor_procedimento > 0
    AND a.start_time BETWEEN %s AND %s
"""

_PARTICULAR_FILTER = """
    p.deleted_at IS NULL
    AND p.created_at BETWEEN %s AND %s
"""

# [SECURITY]: nenhum SELECT abaixo toca `patient_id`, `notes`,
# `clinical_notes_enc` ou qualquer coluna de `patients` — o relatório é
# administrativo e não pode carregar PHI. `metadata_enc`/`dek_encrypted_session`
# só são devolvidos quando a linha foi cifrada sob a TemporaryKey da sessão
# corrente (CASE ... session_key_id = %s), e servem só para extrair a operadora.
_BILLING_CONVENIO_LINES_QUERY = f"""
    SELECT a.id, a.professional_id, pr.name, a.tiss_codigo_procedimento,
           {_CONVENIO_CENTS}, a.start_time,
           CASE WHEN a.session_key_id = %s THEN a.metadata_enc END,
           CASE WHEN a.session_key_id = %s THEN a.dek_encrypted_session END
    FROM appointments a
    LEFT JOIN professionals pr ON pr.id = a.professional_id
    WHERE {_CONVENIO_FILTER}
    ORDER BY a.start_time, a.id
"""

_BILLING_PARTICULAR_LINES_QUERY = f"""
    SELECT p.id, p.appointment_id, a.professional_id, pr.name, p.method,
           {_PARTICULAR_CENTS}, p.created_at
    FROM appointment_payments p
    LEFT JOIN appointments a ON a.id = p.appointment_id
    LEFT JOIN professionals pr ON pr.id = a.professional_id
    WHERE {_PARTICULAR_FILTER}
    ORDER BY p.created_at, p.id
"""

_BILLING_CONVENIO_BY_PROFESSIONAL_QUERY = f"""
    SELECT a.professional_id, pr.name, COUNT(*),
           CAST(COALESCE(SUM({_CONVENIO_CENTS}), 0) AS BIGINT)
    FROM appointments a
    LEFT JOIN professionals pr ON pr.id = a.professional_id
    WHERE {_CONVENIO_FILTER}
    GROUP BY a.professional_id, pr.name
"""

# LEFT JOIN em appointments: pagamento cujo atendimento ainda não sincronizou
# (não há FK na nuvem — 010_drop_sync_fk_constraints.sql) entra com
# professional_id NULL em vez de sumir do total.
_BILLING_PARTICULAR_BY_PROFESSIONAL_QUERY = f"""
    SELECT a.professional_id, pr.name, COUNT(p.id),
           CAST(COALESCE(SUM({_PARTICULAR_CENTS}), 0) AS BIGINT)
    FROM appointment_payments p
    LEFT JOIN appointments a ON a.id = p.appointment_id
    LEFT JOIN professionals pr ON pr.id = a.professional_id
    WHERE {_PARTICULAR_FILTER}
    GROUP BY a.professional_id, pr.name
"""

# A operadora só é conhecida depois de decriptar `metadata_enc` linha a linha,
# então este agrupamento não tem como ser GROUP BY no SQL — a consulta devolve
# só o mínimo (centavos + envelope), sem id de atendimento nem de profissional.
_BILLING_CONVENIO_OPERATOR_ENVELOPES_QUERY = f"""
    SELECT {_CONVENIO_CENTS},
           CASE WHEN a.session_key_id = %s THEN a.metadata_enc END,
           CASE WHEN a.session_key_id = %s THEN a.dek_encrypted_session END
    FROM appointments a
    WHERE {_CONVENIO_FILTER}
"""

# Inclui operadoras com soft delete: o nome histórico continua valendo para
# atendimentos já faturados contra ela.
_INSURANCE_OPERATORS_QUERY = """
    SELECT id, name, ans_code
    FROM insurance_operators
"""


class BillingLine(TypedDict):
    """Lançamento individual de faturamento — sem nenhum dado de paciente."""

    id: str
    appointment_id: str | None
    payment_type: str
    professional_id: str | None
    professional_name: str | None
    operator_id: str | None
    operator_name: str | None
    procedure_code: str | None
    method: str | None
    amount_cents: int
    occurred_at: str | None


class BillingBucket(TypedDict):
    label: str
    total_cents: int
    count: int


class ProfessionalBilling(TypedDict):
    professional_id: str | None
    professional_name: str | None
    convenio_cents: int
    convenio_count: int
    particular_cents: int
    particular_count: int
    total_cents: int


class OperatorBilling(TypedDict):
    operator_id: str | None
    operator_name: str
    ans_code: str | None
    total_cents: int
    count: int


class BillingSummary(TypedDict):
    period: dict[str, str | None]
    currency: str
    note: str
    total_geral: BillingBucket
    convenio: BillingBucket
    particular: BillingBucket
    by_professional: list[ProfessionalBilling]
    by_operator: list[OperatorBilling]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _str_or_none(value: Any) -> str | None:
    return str(value) if value is not None else None


def _extract_insurance_choice(metadata_enc: str | None, dek_encrypted_session: str | None,
                              temp_key: bytes) -> str | None:
    """Devolve SÓ o `insurance_choice` (operator_id normalizado) do metadata
    cifrado do atendimento, ou None se não houver operadora identificável.

    None cobre: linha não cifrada sob a sessão atual (o gateway ainda não
    ressincronizou com esta TemporaryKey), DEK/metadata corrompidos, check-in
    sem escolha explícita (o fallback do desktop é o convênio ativo em
    `patient_insurances`, tabela que não sincroniza para a nuvem) e a escolha
    "particular" num atendimento com valor TISS (dado inconsistente, não
    atribuível a operadora alguma).
    """
    if not metadata_enc or not dek_encrypted_session:
        return None
    try:
        dek = crypto.decrypt_dek(dek_encrypted_session, temp_key)
    except crypto.DecryptionError:
        logger.warning('billing_report_dek_decrypt_failed')
        return None

    # [SECURITY]: o metadata decriptado vive só neste escopo local — nada além
    # de `insurance_choice` sai desta função, e nada dele é logado.
    metadata = _decrypt_optional_json(metadata_enc, dek)
    if not isinstance(metadata, dict):
        return None
    choice = metadata.get('insurance_choice')
    if not isinstance(choice, str):
        return None
    normalized = choice.strip().lower()
    if not normalized or normalized == PAYMENT_TYPE_PARTICULAR:
        return None
    return normalized


def _operator_directory(rows: Iterable[tuple[Any, ...]]) -> dict[str, tuple[str | None, str | None]]:
    """Mapeia operator_id (minúsculo, como string) -> (nome, código ANS)."""
    return {str(op_id).lower(): (name, ans_code) for op_id, name, ans_code in rows}


def _authorize_billing_read(session) -> bytes:
    """Mesmo gate de `read_appointments_report`: escopo da sessão primeiro,
    depois sessão legível + TemporaryKey ainda no cache — qualquer falha é 403."""
    _require_entity_in_scope(session, BILLING_REPORT_ENTITY)
    return _get_temp_key_or_403(session)


def read_billing_report(clinic, session) -> list[BillingLine]:
    """TASK-BO-R02 — lançamentos de faturamento (convênio + particular) da
    janela da sessão, sem nenhum dado de paciente.

    Segue o gate de `read_appointments_report` (escopo `billing`, sessão não
    expirada e entregue, TemporaryKey no cache). Diferente dele, só decripta
    `metadata_enc` para descobrir a operadora — valores e profissional já são
    texto plano na nuvem por não serem PHI.
    """
    temp_key = _authorize_billing_read(session)
    key_id = crypto.session_key_id(temp_key)
    window = (session.date_from, session.date_to)

    convenio_rows, particular_rows, operator_rows = _run_queries(clinic, [
        (_BILLING_CONVENIO_LINES_QUERY, (key_id, key_id, *window)),
        (_BILLING_PARTICULAR_LINES_QUERY, window),
        (_INSURANCE_OPERATORS_QUERY, ()),
    ])
    operators = _operator_directory(operator_rows)

    lines: list[BillingLine] = []
    for (appointment_id, professional_id, professional_name, procedure_code,
         amount_cents, start_time, metadata_enc, dek_encrypted_session) in convenio_rows:
        operator_id = _extract_insurance_choice(metadata_enc, dek_encrypted_session, temp_key)
        operator_name = operators.get(operator_id, (None, None))[0] if operator_id else None
        lines.append({
            'id': str(appointment_id),
            'appointment_id': str(appointment_id),
            'payment_type': PAYMENT_TYPE_CONVENIO,
            'professional_id': _str_or_none(professional_id),
            'professional_name': professional_name,
            'operator_id': operator_id,
            'operator_name': operator_name,
            'procedure_code': procedure_code or None,
            'method': None,
            'amount_cents': int(amount_cents),
            'occurred_at': _iso(start_time),
        })

    for (payment_id, appointment_id, professional_id, professional_name, method,
         amount_cents, created_at) in particular_rows:
        lines.append({
            'id': str(payment_id),
            'appointment_id': _str_or_none(appointment_id),
            'payment_type': PAYMENT_TYPE_PARTICULAR,
            'professional_id': _str_or_none(professional_id),
            'professional_name': professional_name,
            'operator_id': None,
            'operator_name': None,
            'procedure_code': None,
            'method': method,
            'amount_cents': int(amount_cents),
            'occurred_at': _iso(created_at),
        })
    return lines


def _merge_by_professional(convenio_rows: Iterable[tuple[Any, ...]],
                           particular_rows: Iterable[tuple[Any, ...]]) -> list[ProfessionalBilling]:
    """Junta os dois GROUP BY (convênio e particular) numa linha por
    profissional. A chave é o professional_id; None agrupa os lançamentos
    sem profissional resolvível (atendimento ainda não sincronizado)."""
    merged: dict[str | None, ProfessionalBilling] = {}

    def _entry(professional_id: Any, name: str | None) -> ProfessionalBilling:
        key = _str_or_none(professional_id)
        entry = merged.get(key)
        if entry is None:
            entry = {
                'professional_id': key,
                'professional_name': name,
                'convenio_cents': 0,
                'convenio_count': 0,
                'particular_cents': 0,
                'particular_count': 0,
                'total_cents': 0,
            }
            merged[key] = entry
        elif entry['professional_name'] is None and name:
            entry['professional_name'] = name
        return entry

    for professional_id, name, count, cents in convenio_rows:
        entry = _entry(professional_id, name)
        entry['convenio_cents'] += int(cents)
        entry['convenio_count'] += int(count)

    for professional_id, name, count, cents in particular_rows:
        entry = _entry(professional_id, name)
        entry['particular_cents'] += int(cents)
        entry['particular_count'] += int(count)

    for entry in merged.values():
        entry['total_cents'] = entry['convenio_cents'] + entry['particular_cents']

    return sorted(
        merged.values(),
        key=lambda e: (-e['total_cents'], e['professional_name'] or '', e['professional_id'] or ''),
    )


def _group_by_operator(envelope_rows: Iterable[tuple[Any, ...]], temp_key: bytes,
                       operators: dict[str, tuple[str | None, str | None]]) -> list[OperatorBilling]:
    """Agrupa o faturamento de convênio por operadora. Por construção, a soma
    de `total_cents` deste agrupamento é igual ao total de convênio: toda
    linha não atribuível cai no balde "Operadora não identificada"."""
    grouped: dict[str | None, OperatorBilling] = {}
    for amount_cents, metadata_enc, dek_encrypted_session in envelope_rows:
        operator_id = _extract_insurance_choice(metadata_enc, dek_encrypted_session, temp_key)
        bucket = grouped.get(operator_id)
        if bucket is None:
            name, ans_code = operators.get(operator_id, (None, None)) if operator_id else (None, None)
            bucket = {
                'operator_id': operator_id,
                'operator_name': name or UNIDENTIFIED_OPERATOR_LABEL,
                'ans_code': ans_code,
                'total_cents': 0,
                'count': 0,
            }
            grouped[operator_id] = bucket
        bucket['total_cents'] += int(amount_cents)
        bucket['count'] += 1

    return sorted(grouped.values(), key=lambda b: (-b['total_cents'], b['operator_name']))


def read_billing_summary(clinic, session) -> BillingSummary:
    """TASK-BO-R03 — resumo de faturamento da janela da sessão: total geral,
    particular × convênio, por profissional e por operadora.

    Totais e quebra por profissional saem de GROUP BY no Postgres da clínica
    (nada de linha individual trafega para isso). A quebra por operadora é a
    exceção inevitável: a operadora só existe cifrada em `metadata_enc`, então
    a consulta devolve apenas centavos + envelope por atendimento e o
    agrupamento acontece aqui, após extrair só o `insurance_choice`.

    Clínica sem lançamentos no período devolve zeros e listas vazias — nunca
    erro. Mesmo gate de autorização de `read_billing_report`.
    """
    temp_key = _authorize_billing_read(session)
    key_id = crypto.session_key_id(temp_key)
    window = (session.date_from, session.date_to)

    convenio_by_prof, particular_by_prof, envelope_rows, operator_rows = _run_queries(clinic, [
        (_BILLING_CONVENIO_BY_PROFESSIONAL_QUERY, window),
        (_BILLING_PARTICULAR_BY_PROFESSIONAL_QUERY, window),
        (_BILLING_CONVENIO_OPERATOR_ENVELOPES_QUERY, (key_id, key_id, *window)),
        (_INSURANCE_OPERATORS_QUERY, ()),
    ])

    by_professional = _merge_by_professional(convenio_by_prof, particular_by_prof)
    by_operator = _group_by_operator(envelope_rows, temp_key, _operator_directory(operator_rows))

    convenio_cents = sum(p['convenio_cents'] for p in by_professional)
    convenio_count = sum(p['convenio_count'] for p in by_professional)
    particular_cents = sum(p['particular_cents'] for p in by_professional)
    particular_count = sum(p['particular_count'] for p in by_professional)

    return {
        'period': {'from': _iso(session.date_from), 'to': _iso(session.date_to)},
        'currency': 'BRL',
        'note': BILLING_SUMMARY_NOTE,
        'total_geral': {
            'label': TOTAL_LABEL,
            'total_cents': convenio_cents + particular_cents,
            'count': convenio_count + particular_count,
        },
        'convenio': {'label': CONVENIO_LABEL, 'total_cents': convenio_cents, 'count': convenio_count},
        'particular': {'label': PARTICULAR_LABEL, 'total_cents': particular_cents, 'count': particular_count},
        'by_professional': by_professional,
        'by_operator': by_operator,
    }
