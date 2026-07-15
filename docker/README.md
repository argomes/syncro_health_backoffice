# Infra local de homologação (TASK-053)

`docker-compose.yml` (na raiz do repo) sobe Postgres + Redis pra rodar o backoffice
e o gateway conversando de verdade — sem mocks, sem SQLite in-memory.

## Uso

```bash
docker compose up -d       # sobe Postgres+Redis (primeira vez: aplica o schema
                            # de template do gateway automaticamente)
docker compose ps           # confirma os 2 serviços "healthy"
docker compose down         # derruba, mantém os dados no volume
docker compose down -v      # derruba e apaga tudo — próximo `up` reconstrói do zero
```

## O que acontece no primeiro `up`

1. Postgres sobe vazio.
2. `docker/postgres-init/01-clinic-template.sh` roda automaticamente (só no primeiro
   boot, via `docker-entrypoint-initdb.d`): cria um banco `clinic_schema_template`,
   aplica os 5 arquivos SQL de `../SyncroHealth/syncro_gateway/internal/adapters/output/cloud/migrations/`
   (montados como volume read-only), e marca esse banco como `TEMPLATE` do Postgres.
3. A partir daí, toda vez que `clinics/provisioning.py::provision_clinic_database()`
   rodar com `CLINIC_DB_TEMPLATE=clinic_schema_template` no `.env`, o banco da
   clínica nasce **já com o schema completo** (patients/appointments/dual envelope),
   em vez de vazio — sem isso, o gateway não teria onde gravar dados sincronizados.

## Pressuposto de layout

Este compose espera que `syncro_gateway` esteja clonado como repositório irmão
deste (`../SyncroHealth/syncro_gateway`) — é de lá que o schema Postgres é lido.
Se o layout for diferente na sua máquina, ajuste o bind mount em `docker-compose.yml`.

## Variáveis no `.env`

```
PROVISIONING_DATABASE_URL=postgres://superuser:senha@localhost:5432/postgres
CLINIC_DB_TEMPLATE=clinic_schema_template
CACHE_URL=redis://localhost:6379/1
```

## Validado manualmente (2026-07-14)

- `provision_clinic_database()` roda de verdade contra este Postgres (via o signal
  automático `post_save` em `Clinic.objects.create()`) — confirmado: `provisioning_status`
  vira `provisioned`, `db_name`/`db_user`/`db_password_encrypted` preenchidos, o banco
  da clínica já contém as tabelas do schema (`\dt` mostra `patients`, `appointments` etc.),
  e o ACL confirma isolamento (`REVOKE ALL ... FROM PUBLIC` efetivo — só o usuário da
  própria clínica tem acesso).
- `deprovision_clinic_database()` remove o banco/usuário de verdade.
- Cache Django (`django.core.cache`) funcionando contra o Redis real (roundtrip
  `cache.set`/`cache.get` confirmado, chaves visíveis via `redis-cli`).
