import logging
import httpx
from .service_tokens import generate_service_token

logger = logging.getLogger(__name__)


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
        with httpx.Client(timeout=5.0) as client:
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
