import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
import jwt
from django.conf import settings
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization


def generate_or_load_keys() -> tuple[bytes, bytes]:
    """
    Retorna (private_key_pem, public_key_pem).
    Prioriza variáveis de ambiente/settings. Caso não existam, carrega dos arquivos locais.
    Se os arquivos não existirem, gera um novo par RSA de 2048 bits e os salva.
    """
    private_key_pem = getattr(settings, 'SERVICE_TOKEN_PRIVATE_KEY', None) or os.environ.get('SERVICE_TOKEN_PRIVATE_KEY', '').strip()
    public_key_pem = getattr(settings, 'SERVICE_TOKEN_PUBLIC_KEY', None) or os.environ.get('SERVICE_TOKEN_PUBLIC_KEY', '').strip()

    if private_key_pem and public_key_pem:
        return private_key_pem.encode(), public_key_pem.encode()

    base_dir = Path(settings.BASE_DIR)
    priv_path = base_dir / '.service_jwt_private.pem'
    pub_path = base_dir / '.service_jwt_public.pem'

    if priv_path.exists() and pub_path.exists():
        return priv_path.read_bytes(), pub_path.read_bytes()

    # Gera nova chave RSA
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    priv_path.write_bytes(private_pem)
    pub_path.write_bytes(public_pem)

    # Tenta adicionar ao .gitignore para segurança
    gitignore = base_dir / '.gitignore'
    if gitignore.exists():
        try:
            content = gitignore.read_text()
            if '.service_jwt_private.pem' not in content:
                with open(gitignore, 'a') as f:
                    f.write('\n# Service JWT Keys\n.service_jwt_private.pem\n.service_jwt_public.pem\n')
        except Exception:
            pass

    return private_pem, public_pem


def generate_service_token(clinic_id: str, plan: str, active_modules: list[str], expires_in_hours: int = 24) -> str:
    """
    Gera o Service Token (JWT) assinado com RSA-256 contendo clinic_id, plan, active_modules e exp.
    """
    private_pem, _ = generate_or_load_keys()

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=expires_in_hours)

    payload = {
        'clinic_id': clinic_id,
        'plan': plan,
        'active_modules': active_modules,
        'exp': int(expires_at.timestamp()),
        'iat': int(now.timestamp()),
    }

    return jwt.encode(payload, private_pem, algorithm='RS256')


def decode_service_token(token: str) -> dict:
    """
    Decodifica e valida o Service Token com a chave pública RSA.
    Pode levantar jwt.ExpiredSignatureError ou jwt.InvalidTokenError.
    """
    _, public_pem = generate_or_load_keys()
    return jwt.decode(token, public_pem, algorithms=['RS256'])
