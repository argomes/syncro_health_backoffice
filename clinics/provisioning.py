import base64
import logging
import re
import secrets
import string

import psycopg2
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.conf import settings

logger = logging.getLogger(__name__)

# Identificadores PostgreSQL: apenas letras minúsculas, dígitos e underscore.
# Máximo 63 chars (limite do Postgres). Deve começar com letra.
_IDENT_RE = re.compile(r'^[a-z][a-z0-9_]{0,62}$')


def _validate_pg_identifier(name: str) -> str:
    """Levanta ValueError se o identificador não for seguro para uso em DDL."""
    if not _IDENT_RE.match(name):
        raise ValueError(f"Identificador PostgreSQL inválido: {name!r}")
    return name


def _quote_ident(name: str) -> str:
    """Retorna o identificador entre aspas duplas.
    Seguro porque _validate_pg_identifier garante [a-z][a-z0-9_]+ — sem chars especiais."""
    return f'"{name}"'


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
    Levanta ValueError se o slug gerar identificadores inválidos.
    Levanta RuntimeError em caso de falha no Postgres.
    """
    slug = clinic.slug.replace('-', '_').lower()
    db_name = _validate_pg_identifier(f"clinic_{slug}")
    db_user = _validate_pg_identifier(f"u_{slug}")
    password = _generate_password()

    conn = _superuser_conn()
    try:
        cur = conn.cursor()
        q_user = _quote_ident(db_user)
        q_db = _quote_ident(db_name)

        # Idempotente: uma clínica pode ser reprovisionada com o mesmo
        # db_name/db_user (ex.: suíte E2E de sync, CROSS-017.1, que reseta
        # provisioning_status a cada rodada mas nunca dropa o Postgres real).
        # Sem isso, CREATE USER/CREATE DATABASE falham com "already exists"
        # e o provisionamento fica preso em status=failed indefinidamente.
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (db_user,))
        user_exists = cur.fetchone() is not None
        if user_exists:
            cur.execute(f"ALTER USER {q_user} WITH PASSWORD %s", (password,))
        else:
            cur.execute(f"CREATE USER {q_user} WITH PASSWORD %s", (password,))

        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
        db_exists = cur.fetchone() is not None
        if not db_exists:
            # CLINIC_DB_TEMPLATE (opcional): se configurado, o novo banco já nasce com o
            # schema do gateway aplicado (patients/appointments/dual envelope — ver
            # syncro_gateway/internal/adapters/output/cloud/migrations/*.sql), clonado via
            # Postgres TEMPLATE em vez de rodar as migrations manualmente depois. Sem isso
            # configurado, o comportamento é o mesmo de antes (banco vazio) — não quebra
            # ambientes que ainda não têm o template criado (ex.: Railway em produção, até
            # decidirmos formalizar isso lá também).
            template = getattr(settings, 'CLINIC_DB_TEMPLATE', '')
            if template:
                q_template = _quote_ident(_validate_pg_identifier(template))
                cur.execute(f"CREATE DATABASE {q_db} OWNER {q_user} TEMPLATE {q_template}")
                # CREATE DATABASE ... TEMPLATE clona os catálogos preservando o dono
                # ORIGINAL de cada tabela — que é "clinic_template_owner" (ver
                # docker/postgres-init/01-clinic-template.sh), não o q_user recém-criado.
                # OWNER acima só define o dono do objeto "database", não das tabelas
                # dentro dele. Sem este GRANT, o usuário da clínica recebe
                # "permission denied" em toda tabela ao se conectar pela primeira vez.
                # Membros de uma role herdam (INHERIT, padrão do Postgres) os
                # privilégios de dono automaticamente — não é preciso GRANT por tabela.
                cur.execute(f"GRANT clinic_template_owner TO {q_user}")
            else:
                cur.execute(f"CREATE DATABASE {q_db} OWNER {q_user}")

        cur.execute(f"REVOKE ALL ON DATABASE {q_db} FROM PUBLIC")
        cur.close()
    except Exception as exc:
        logger.error("provisioning_failed clinic_id=%s", str(clinic.id))
        raise RuntimeError(str(exc)) from exc
    finally:
        conn.close()

    encrypted = encrypt_with_public_key(clinic.public_key_pem, password)
    password = None  # plaintext descartado imediatamente após uso

    return db_name, db_user, encrypted


def deprovision_clinic_database(db_name: str, db_user: str) -> None:
    """
    Remove database e usuário PostgreSQL da clínica.
    Chamado apenas ao deletar uma clínica.
    Levanta ValueError se os identificadores forem inválidos.
    """
    _validate_pg_identifier(db_name)
    _validate_pg_identifier(db_user)

    conn = _superuser_conn()
    try:
        cur = conn.cursor()
        q_db = _quote_ident(db_name)
        q_user = _quote_ident(db_user)
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
            (db_name,),
        )
        cur.execute(f"DROP DATABASE IF EXISTS {q_db}")
        cur.execute(f"DROP USER IF EXISTS {q_user}")
        cur.close()
    except Exception as exc:
        logger.error("deprovisioning_failed db=%s", db_name)
        raise RuntimeError(str(exc)) from exc
    finally:
        conn.close()
