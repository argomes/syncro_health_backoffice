"""
Conexão dinâmica ao Postgres exclusivo de uma clínica, para leitura de relatórios
(TASK-043, redesenhado em BACFF-AVULSA-06).

Decisão de design (BACFF-AVULSA-06): o Backoffice NÃO usa mais o superuser
Postgres (`PROVISIONING_DATABASE_URL`) para ler o banco de uma clínica — isso
violava o requisito de que o operador não deve ter acesso irrestrito a todos
os bancos sem autorização explícita da clínica.

Em vez disso, conectamos como o `db_user` escopado da própria clínica (criado
em clinics/provisioning.py, sem SUPERUSER/CREATEDB/CREATEROLE, sem GRANT
cruzado a outros bancos), usando uma senha temporária ("grant") que a própria
clínica autoriza e envia via gateway local — a única parte do sistema que
detém a chave privada RSA capaz de decriptar `db_password_encrypted`.

O grant chega pelo endpoint `POST /api/clinics/db-access-grant/`
(clinics/views.py::ClinicViewSet.db_access_grant) e vive SÓ em
django.core.cache (Redis), chave `clinic_db_grant:{clinic.id}`, com TTL nativo
do cache — nunca é persistido em modelo relacional, e expira sozinho sem
depender de job de limpeza.

Sem grant vigente, `clinic_db_connection()` levanta `PermissionDenied`
explicitamente — nunca cai de volta para o superuser.

`PROVISIONING_DATABASE_URL` não é referenciado neste módulo. Ele continua
existindo, mas é de uso exclusivo de clinics/provisioning.py (criar/dropar
databases e usuários por clínica).
"""
from contextlib import contextmanager

import psycopg2
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import PermissionDenied

from clinics.provisioning import _validate_pg_identifier


def _build_dsn(clinic, password):
    host = settings.PROVISIONING_HOST
    port = getattr(settings, 'PROVISIONING_PORT', 5432)
    db_name = _validate_pg_identifier(clinic.db_name)  # nunca interpolar sem validar
    db_user = _validate_pg_identifier(clinic.db_user)

    return f"postgresql://{db_user}:{password}@{host}:{port}/{db_name}"


@contextmanager
def clinic_db_connection(clinic):
    """
    Abre uma conexão psycopg2 somente-leitura ao banco exclusivo da clínica,
    autenticada como o `db_user` escopado da própria clínica, usando a senha
    do grant temporário vigente (BACFF-AVULSA-06).

    `readonly=True` é defesa em profundidade — este caminho de código nunca
    deveria escrever, e isso garante que uma query mal escrita não consiga.

    Levanta `PermissionDenied` se não houver grant vigente no cache (expirado
    ou nunca concedido) — o acesso ao banco da clínica depende de autorização
    explícita e recente da própria clínica via gateway local.
    """
    grant = cache.get(f"clinic_db_grant:{clinic.id}")
    if not grant:
        raise PermissionDenied(
            "Acesso ao banco da clínica não autorizado no momento — "
            "peça à clínica para liberar via gateway local."
        )

    dsn = _build_dsn(clinic, grant['password'])

    conn = psycopg2.connect(dsn)
    try:
        conn.set_session(readonly=True, autocommit=True)
        yield conn
    finally:
        conn.close()
