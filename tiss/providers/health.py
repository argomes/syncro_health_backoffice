"""
Sonda ativa compartilhada entre providers (§4.4(b) do documento de
arquitetura).

Deliberadamente genérica: um GET barato no descritor do serviço (`?wsdl`),
com timeout curto, sem credencial e sem dado de paciente. Um provider novo
que fale SOAP sobre HTTP reusa isto e ganha "Testar conexão" no admin de
graça; um provider com mecanismo próprio implementa o seu `health_check`
sem herdar nada.

O que esta sonda NÃO faz, de propósito:
- Não dispara consulta de elegibilidade/autorização real. Isso tem custo
  contratual com a operadora e usaria dado de beneficiário para uma
  finalidade (monitoramento nosso) que o titular não consentiu.
- Não roda periodicamente. Probe ativo agendado (Celery Beat) exigiria
  credencial de clínica-cliente para monitoramento nosso — problema de LGPD
  e de contrato — e pode esbarrar em rate limit/cobrança por consulta. Só
  quando tivermos credencial de homologação PRÓPRIA. A fonte de verdade
  contínua é o caminho passivo (`OperatorCallLog`, §4.4(a)).
"""
import logging
import time

import httpx
from django.conf import settings

from .base import ProviderHealth

logger = logging.getLogger(__name__)

HEALTH_TIMEOUT = 5.0


def _mock_enabled() -> bool:
    return bool(getattr(settings, 'TISS_SOAP_MOCK', False))


def wsdl_health_check(endpoint_url: str, provider_nome: str) -> ProviderHealth:
    """
    GET no `?wsdl` do endpoint. Nunca levanta exceção — indisponibilidade é
    um RESULTADO (`reachable=False`), não um erro de programa: quem chama é
    um botão de admin que precisa renderizar uma resposta.

    `detail` recebe só o tipo da exceção / status HTTP, nunca o corpo da
    resposta nem a URL com querystring — endpoint de operadora não é PII,
    mas resposta de operadora pode ser.
    """
    if not endpoint_url:
        return ProviderHealth(reachable=False, detail='endpoint_url_vazio')

    if _mock_enabled():
        # Mesmo padrão dos clients (TISS_SOAP_MOCK): sem I/O de rede em
        # teste/dev, resposta determinística.
        return ProviderHealth(reachable=True, latency_ms=0, detail='mock')

    url = endpoint_url if '?' in endpoint_url else f'{endpoint_url}?wsdl'
    inicio = time.perf_counter()
    try:
        resp = httpx.get(url, timeout=HEALTH_TIMEOUT)
    except httpx.HTTPError as exc:
        latency_ms = int((time.perf_counter() - inicio) * 1000)
        logger.warning('%s: health_check falhou (%s)', provider_nome, type(exc).__name__)
        return ProviderHealth(reachable=False, latency_ms=latency_ms, detail=type(exc).__name__)

    latency_ms = int((time.perf_counter() - inicio) * 1000)
    return ProviderHealth(
        reachable=200 <= resp.status_code < 400,
        latency_ms=latency_ms,
        detail=f'http_{resp.status_code}',
    )
