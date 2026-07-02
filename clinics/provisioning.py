import base64
import logging
import secrets
import string

import psycopg2
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.conf import settings

logger = logging.getLogger(__name__)


def _generate_password(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def _superuser_conn():
    url = settings.PROVISIONING_DATABASE_URL
    if not url:
        raise RuntimeError("PROVISIONING_DATABASE_URL não configurado")
    conn = psycopg2.connect(url)
    conn.autocommit = True
    return conn


def encrypt_with_public_key(public_key_pem: str, plaintext: str) -> str:
    """
    Criptografa plaintext com a chave pública RSA da clínica.
    Retorna base64 do ciphertext — ilegível sem a chave privada do app desktop.
    """
    public_key = serialization.load_pem_public_key(public_key_pem.encode())
    ciphertext = public_key.encrypt(
        plaintext.encode(),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(ciphertext).decode()


def provision_clinic_database(clinic) -> tuple[str, str, str]:
    """
    Cria database e usuário PostgreSQL para a clínica.
    Retorna (db_name, db_user, senha_criptografada).
    A senha em plaintext é descartada após criptografia.
    Levanta RuntimeError em caso de falha.
    """
    db_name = f"clinic_{clinic.slug.replace('-', '_')}"
    db_user = f"u_{clinic.slug.replace('-', '_')}"
    password = _generate_password()

    conn = _superuser_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"CREATE USER {db_user} WITH PASSWORD %s", (password,))
        cur.execute(f"CREATE DATABASE {db_name} OWNER {db_user}")
        cur.execute(f"REVOKE ALL ON DATABASE {db_name} FROM PUBLIC")
        cur.close()
    except Exception as exc:
        logger.error("provisioning_failed clinic_id=%s", str(clinic.id))
        raise RuntimeError(str(exc)) from exc
    finally:
        conn.close()

    # Criptografa com a chave pública da clínica — senha em plaintext descartada aqui
    encrypted = encrypt_with_public_key(clinic.public_key_pem, password)
    password = None  # garante que não fica na memória além do necessário

    return db_name, db_user, encrypted


def deprovision_clinic_database(db_name: str, db_user: str) -> None:
    """
    Remove database e usuário PostgreSQL da clínica.
    Chamado apenas ao deletar uma clínica.
    """
    conn = _superuser_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
            (db_name,),
        )
        cur.execute(f"DROP DATABASE IF EXISTS {db_name}")
        cur.execute(f"DROP USER IF EXISTS {db_user}")
        cur.close()
    except Exception as exc:
        logger.error("deprovisioning_failed db=%s", db_name)
        raise RuntimeError(str(exc)) from exc
    finally:
        conn.close()
