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


def _is_safe_url(url: str) -> bool:
    """Retorna True apenas se a URL usa http/https e não aponta para IP privado/loopback."""
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return False
    if not parsed.hostname:
        return False
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(parsed.hostname))
        return not any(ip in net for net in _BLOCKED_NETWORKS)
    except Exception:
        return False


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

    if not _is_safe_url(clinic.gateway_url):
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

    url = f"{clinic.gateway_url.rstrip('/')}/api/v1/sync/modules"
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
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
