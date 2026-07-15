"""
Conexão dinâmica ao Postgres exclusivo de uma clínica, para leitura de relatórios
(TASK-043).

Decisão de design: NÃO usamos `clinic.db_password_encrypted` — essa senha é
cifrada com a chave pública RSA da própria clínica (clinics/provisioning.py::
encrypt_with_public_key), de propósito só decriptável pela chave privada que
vive no gateway (Local-First). O Backoffice nunca teve acesso a essa senha em
claro por desenho, e TASK-043 não muda isso.

Em vez disso, reaproveitamos a mesma conexão de superusuário Postgres já usada
em clinics/provisioning.py (`PROVISIONING_DATABASE_URL`) — a que já cria/dropa
databases e usuários por clínica — só trocando o `dbname` para o da clínica
alvo. Um superuser Postgres pode conectar a qualquer database no cluster
independente de senha de usuário comum, então isso não introduz nenhum segredo
novo nem contorna nenhuma proteção que já não existisse (quem tem
PROVISIONING_DATABASE_URL já tem acesso irrestrito a todos os bancos).
"""
from contextlib import contextmanager
from urllib.parse import urlparse, urlunparse

import psycopg2
from django.conf import settings

from clinics.provisioning import _validate_pg_identifier


@contextmanager
def clinic_db_connection(clinic):
    """
    Abre uma conexão psycopg2 somente-leitura ao banco exclusivo da clínica.
    `readonly=True` é defesa em profundidade — este caminho de código nunca
    deveria escrever, e isso garante que uma query mal escrita não consiga.
    """
    base_url = settings.PROVISIONING_DATABASE_URL
    if not base_url:
        raise RuntimeError('PROVISIONING_DATABASE_URL não configurado')

    db_name = _validate_pg_identifier(clinic.db_name)  # nunca interpolar sem validar

    parsed = urlparse(base_url)
    dsn = urlunparse(parsed._replace(path=f'/{db_name}'))

    conn = psycopg2.connect(dsn)
    try:
        conn.set_session(readonly=True, autocommit=True)
        yield conn
    finally:
        conn.close()
