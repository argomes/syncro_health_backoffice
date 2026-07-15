#!/bin/bash
# TASK-053 — cria um banco "clinic_schema_template" com o schema do gateway
# (patients/appointments/dual envelope) já aplicado, e marca como TEMPLATE
# Postgres. provisioning.py::provision_clinic_database usa esse template
# (via CLINIC_DB_TEMPLATE no .env) para que cada clínica provisionada já
# nasça com o schema pronto, em vez de um banco vazio.
#
# Roda automaticamente só no primeiro boot do container (docker-entrypoint-initdb.d),
# com o volume de dados vazio — se o volume já existir, este script NÃO roda de novo
# (comportamento padrão da imagem oficial do Postgres).
set -euo pipefail

MIGRATIONS_DIR="/gateway-migrations"

# clinic_template_owner: role NOLOGIN dona de todas as tabelas do template.
# CREATE DATABASE ... TEMPLATE clona os catálogos preservando o dono original
# dos objetos — o OWNER passado em CREATE DATABASE só afeta o dono do objeto
# "database" em si, NÃO das tabelas dentro dele. Sem isso, o usuário da
# clínica recebe "permission denied" em toda tabela, porque elas continuariam
# pertencendo a $POSTGRES_USER. provisioning.py concede essa role ao usuário
# da clínica logo após o clone (GRANT clinic_template_owner TO u_xxx); membros
# de uma role têm INHERIT por padrão no Postgres, então herdam os privilégios
# de dono automaticamente, sem precisar de GRANT tabela a tabela. Isso é
# seguro porque o acesso ao banco da clínica já é isolado via
# REVOKE ALL ON DATABASE ... FROM PUBLIC (feito em provisioning.py) — a role
# compartilhada só importa dentro de um banco ao qual o usuário já pode se
# conectar.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'clinic_template_owner') THEN
            CREATE ROLE clinic_template_owner NOLOGIN;
        END IF;
    END
    \$\$;

    CREATE DATABASE clinic_schema_template;
EOSQL

# O schema "public" nasce pertencendo a $POSTGRES_USER (quem criou o banco).
# Sem transferir a posse, SET ROLE clinic_template_owner abaixo não teria
# CREATE nesse schema (Postgres 15+ não concede CREATE em "public" a PUBLIC
# por padrão) e as migrations falhariam com "permission denied for schema public".
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -d clinic_schema_template <<-EOSQL
    ALTER SCHEMA public OWNER TO clinic_template_owner;
EOSQL

# As migrations rodam com SET ROLE clinic_template_owner na mesma sessão, para
# que as tabelas já nasçam pertencendo a essa role em vez de $POSTGRES_USER.
# (Tentamos originalmente REASSIGN OWNED BY $POSTGRES_USER depois de criar as
# tabelas, mas o Postgres recusa: $POSTGRES_USER é o superuser de bootstrap do
# cluster, dono de objetos exigidos pelo próprio sistema, e REASSIGN OWNED BY
# nele falha com "cannot reassign ownership ... required by the database
# system". SET ROLE antes de cada -f evita o problema na origem.)
for f in $(ls "$MIGRATIONS_DIR"/*.sql | sort); do
    echo "[clinic-template] aplicando $f"
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -d clinic_schema_template \
        -c "SET ROLE clinic_template_owner;" -f "$f"
done

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    UPDATE pg_database SET datistemplate = TRUE WHERE datname = 'clinic_schema_template';
    -- datallowconn = FALSE evita que CREATE DATABASE ... TEMPLATE falhe de forma
    -- intermitente por causa de conexões ativas no template (ex.: alguém abrindo
    -- um client SQL nele por engano). Postgres permite clonar um template com
    -- datallowconn=false — é assim que template0/template1 funcionam — mas
    -- bloqueia conexões normais.
    UPDATE pg_database SET datallowconn = FALSE WHERE datname = 'clinic_schema_template';
EOSQL

echo "[clinic-template] pronto — clinic_schema_template marcado como TEMPLATE"
