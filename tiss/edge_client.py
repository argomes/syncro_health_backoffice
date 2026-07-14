"""
BO-08.2 — Cliente REST do Backoffice para o Edge Gateway (app desktop local
da clínica). Confirmado por leitura de clinics/services.py::sync_clinic_modules
que a direção da integração hoje é sempre Backoffice -> Edge (o backoffice
é quem inicia a chamada HTTP contra `clinic.gateway_url`, autenticando com um
Service Token JWT de curta duração via clinics.service_tokens). Este módulo
segue exatamente esse padrão para os dois endpoints de exportação de guias
descritos na TASK-BO-08 (que vivem no Edge, em Go — fora deste repositório):

  GET  /api/v1/tiss/guias?competencia=YYYY-MM&status=nao_enviada
  POST /api/v1/tiss/guias/marcar-enviadas

Reaproveita a validação de SSRF (_is_safe_url) de clinics/services.py.
"""
import logging

import httpx

from clinics.service_tokens import generate_service_token
from clinics.services import _is_safe_url

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0


class EdgeClientError(Exception):
    """Erro ao comunicar com o Edge Gateway da clínica (rede, HTTP, gateway_url ausente/bloqueada)."""


def _client_for(clinic) -> tuple[str, dict]:
    if not clinic.gateway_url:
        raise EdgeClientError('clinic_sem_gateway_url')
    if not _is_safe_url(clinic.gateway_url):
        # Nunca logar a URL completa com detalhes sensíveis — mas a gateway_url
        # em si não é PHI, só um endereço local da clínica.
        logger.error('edge_client bloqueado: gateway_url inválida/privada para clínica %s', str(clinic.id))
        raise EdgeClientError('gateway_url_blocked')

    token = generate_service_token(
        clinic_id=str(clinic.id),
        plan=clinic.plan,
        active_modules=clinic.active_modules,
        expires_in_hours=1,
    )
    headers = {'Authorization': f'Bearer {token}'}
    return clinic.gateway_url.rstrip('/'), headers


def fetch_guias_para_faturamento(clinic, competencia: str, status: str = 'nao_enviada') -> list[dict]:
    """
    GET /api/v1/tiss/guias?competencia=YYYY-MM&status=... no Edge da clínica.
    Retorna a lista de guias (dict) prontas para faturamento — dados TUSS,
    CBO, CID10, carteirinha etc. já preenchidos pelo Edge a partir do
    prontuário local. Nunca loga o conteúdo da resposta (pode conter PHI).
    """
    base_url, headers = _client_for(clinic)
    try:
        resp = httpx.get(
            f'{base_url}/api/v1/tiss/guias',
            params={'competencia': competencia, 'status': status},
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error('fetch_guias_para_faturamento falhou para clínica %s: %s', str(clinic.id), type(exc).__name__)
        raise EdgeClientError('edge_request_failed') from exc

    data = resp.json()
    return data.get('guias', data if isinstance(data, list) else [])


def marcar_guias_enviadas(clinic, appointment_ids: list[str], tiss_lote_id: str) -> bool:
    """
    POST /api/v1/tiss/guias/marcar-enviadas no Edge da clínica. Marca
    appointment.metadata.tiss_lote_id = tiss_lote_id para os appointment_ids
    informados, uma vez que o envio SOAP tenha sido confirmado (protocolo
    recebido). Retorna True se o Edge confirmou (2xx).
    """
    base_url, headers = _client_for(clinic)
    try:
        resp = httpx.post(
            f'{base_url}/api/v1/tiss/guias/marcar-enviadas',
            json={'appointment_ids': appointment_ids, 'tiss_lote_id': tiss_lote_id},
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        logger.error('marcar_guias_enviadas falhou para clínica %s: %s', str(clinic.id), type(exc).__name__)
        raise EdgeClientError('edge_request_failed') from exc
