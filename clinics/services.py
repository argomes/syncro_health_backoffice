import ipaddress
import logging
import socket
from urllib.parse import urlparse

import httpx

from .service_tokens import generate_service_token

logger = logging.getLogger(__name__)

_BLOCKED_NETWORKS = [
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('169.254.0.0/16'),  # AWS IMDS / link-local
    ipaddress.ip_network('::1/128'),
    ipaddress.ip_network('fc00::/7'),
]


def _resolve_safe_ip(url: str):
    """
    Resolve o hostname da URL UMA ÚNICA VEZ e retorna o IP resolvido se ele
    não estiver em rede bloqueada (privada/loopback/link-local). Retorna
    None se a URL for inválida ou o IP resolvido for bloqueado.

    Resolver o IP aqui e reutilizá-lo na requisição real (em vez de deixar o
    httpx resolver o DNS de novo) evita janela de DNS rebinding: um atacante
    que controle o DNS do hostname poderia apontar para um IP público na
    validação e trocar para um IP bloqueado (ex.: 169.254.169.254, IMDS da
    AWS) bem a tempo da requisição real.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return None
    if not parsed.hostname:
        return None
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(parsed.hostname))
    except Exception:
        return None
    if any(ip in net for net in _BLOCKED_NETWORKS):
        return None
    return ip


def _is_safe_url(url: str) -> bool:
    """Retorna True apenas se a URL usa http/https e não aponta para IP privado/loopback."""
    return _resolve_safe_ip(url) is not None


def sync_clinic_modules(clinic) -> bool:
    """
    Envia a lista de módulos ativos para o Edge Gateway local da clínica.
    Faz um POST /api/v1/sync/modules no gateway_url da clínica.
    Autentica usando o Service Token da clínica.
    Retorna True se sucesso, False caso contrário.
    """
    if not clinic.gateway_url:
        logger.info("sync_clinic_modules ignorado: sem gateway_url configurado para clínica %s", str(clinic.id))
        return False

    safe_ip = _resolve_safe_ip(clinic.gateway_url)
    if safe_ip is None:
        logger.error(
            "sync_clinic_modules bloqueado: gateway_url inválida ou IP privado na clínica %s url=%s",
            str(clinic.id),
            clinic.gateway_url,
        )
        raise ValueError('gateway_url_blocked')

    token = generate_service_token(
        clinic_id=str(clinic.id),
        plan=clinic.plan,
        active_modules=clinic.active_modules,
        expires_in_hours=1,  # Token temporário de 1 hora
    )

    parsed = urlparse(clinic.gateway_url.rstrip('/'))
    # Usa o IP JÁ resolvido e validado (não o hostname) na requisição real —
    # se deixássemos o httpx resolver o DNS de novo aqui, um atacante que
    # controle o DNS do hostname poderia trocar a resposta entre a validação
    # acima e esta chamada (DNS rebinding) e escapar do bloqueio de SSRF.
    netloc = f'{safe_ip}:{parsed.port}' if parsed.port else str(safe_ip)
    resolved_url = parsed._replace(netloc=netloc).geturl()
    url = f"{resolved_url}/api/v1/sync/modules"
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Host': parsed.hostname,
    }
    payload = {
        'clinic_id': str(clinic.id),
        'modules': clinic.active_modules if clinic.status == 'active' else [],
    }

    try:
        logger.info("sync_clinic_modules: enviando %s para %s", payload, url)
        # Timeout curto para não travar workers ou requisições HTTP do backoffice
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            logger.info("sync_clinic_modules: sucesso para clínica %s", str(clinic.id))
            return True
    except Exception as exc:
        logger.warning(
            "sync_clinic_modules: falhou ao conectar/enviar para %s na clínica %s. Erro: %s",
            url, str(clinic.id), str(exc)
        )
        return False
