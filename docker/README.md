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

## CROSS-017.0 — subir o backoffice Django e provar sync real gateway↔backoffice (2026-07-17)

Sub-task de infra do CROSS-017 (`.claude/tasks/CROSS-TASKS.md` no SyncroHealth). Objetivo:
provar que dá pra rodar o backoffice Django de verdade contra este Postgres/Redis e que o
loop completo de provisionamento (o mesmo que o app desktop/gateway usa em produção)
funciona ponta a ponta — sem ainda escrever specs de tela (isso é CROSS-017.1+).

### Como subir o backoffice local

```bash
cd syncro-backoffice
docker compose up -d              # Postgres+Redis (se ainda não estiver rodando)
source .venv/bin/activate         # venv já existe neste repo
python manage.py check            # sanity check — deve rodar limpo contra o .env atual
python manage.py runserver 8000
```

O `.env` do repo já aponta pro Postgres/Redis do compose (`PROVISIONING_DATABASE_URL`,
`CLINIC_DB_TEMPLATE=clinic_schema_template`, `CACHE_URL`). O banco do próprio backoffice
(`DATABASE_URL`) continua SQLite local — isso é correto, só o Postgres de provisionamento
de clínicas precisa ser o real; não confundir os dois.

### Loop de provisionamento validado manualmente ponta a ponta

Isso reproduz exatamente o que o gateway faz no primeiro boot com `BackofficeURL`/`LicenseKey`
configurados (`syncro_gateway/internal/adapters/output/backoffice/license_client.go`):

1. Criar a clínica no backoffice (equivalente ao cadastro comercial que hoje é manual/admin):
   `Clinic.objects.create(name=..., slug=..., cnpj=...)` → nasce com `provisioning_status=waiting_key`
   e um `license_key` (UUID) gerado automaticamente.
2. O gateway gera seu par de chaves RSA (isso é o que `setup.Service.Initialize()` faz hoje,
   ver `syncro_gateway/internal/core/services/setup/service.go`) e registra a pública:
   `POST /api/clinics/register-key/` (⚠️ não é `/api/v1/clinics/...` — o prefixo real do
   router de clínicas é `/api/clinics/`, confirmado batendo direto no endpoint) com
   header `X-License-Key: <license_key>` e body `{"public_key_pem": "..."}`.
3. Esse POST dispara o signal `post_save` em `clinics/signals.py`, que chama
   `provisioning.py::provision_clinic_database()` de verdade contra o Postgres do compose.
   Confirmado nesta rodada: `provisioning_status` vira `provisioned`, e
   `docker exec syncro-backoffice-postgres-1 psql -U superuser -d clinic_<slug> -c '\dt'`
   mostra o schema completo já aplicado (patients, appointments, professionals,
   schedule_availability, clinic_settings, edge_registry, etc. — o mesmo schema de
   template descrito acima).
4. Cleanup: `clinics/provisioning.py::deprovision_clinic_database(db_name, db_user)` (assinatura
   real recebe `db_name`/`db_user`, não a instância `clinic`) remove banco+usuário de verdade.

Isso prova que a comunicação Django↔Postgres via `provisioning.py` funciona de ponta a ponta
contra o ambiente Docker local, e que o par de endpoints que o gateway usa
(`register-key`/`credentials`, ambos em `clinics/views.py`) está no ar e funcional.

### O que NÃO foi feito nesta rodada (pendente para continuar)

- **Não** subi o gateway Go apontando pra este backoffice local (`TEST_MODE=false` +
  `BACKOFFICE_URL=http://localhost:8000` + `LICENSE_KEY=<da clínica criada>`). O smoke test
  acima reproduziu manualmente (via Django shell + curl) os dois passos que o gateway faria
  no boot (`bootstrapCloudWithLicense` em `syncro_gateway/cmd/gateway/bootstrap.go:676`),
  para isolar e confirmar rápido que o lado backoffice está correto antes de mexer no gateway.
  Rodar o gateway real de ponta a ponta contra isso é o próximo passo natural — não tem
  bloqueio técnico conhecido, só não coube nesta rodada.
- **Decisão de arquitetura para o item 2 do pedido original (como o gateway aponta pro
  backoffice local sem quebrar a suíte E2E atual)**: NÃO criei ainda `TEST_MODE_WITH_SYNC`
  nem `playwright.config.sync.ts`. Meu diagnóstico depois de ler `config.go`/`bootstrap.go`:
  `TEST_MODE=true` hoje é tudo-ou-nada — além de pular o bootstrap de cloud
  (`!cfg.TestMode && cfg.BackofficeURL != ""`), ele força `LocalDBPath: ":memory:"` e
  `ClinicKey` fixa de teste, e o `RegisterRoutes(..., cfg.TestMode)` liga o bypass do rate
  limiter de login. Ou seja, **não dá pra simplesmente setar `BACKOFFICE_URL` com
  `TEST_MODE=true`** — o bootstrap de cloud está fisicamente dentro do `if !cfg.TestMode`.
  Duas rotas possíveis, nenhuma implementada ainda:
  (a) rodar o gateway com `TEST_MODE=false` de verdade (LOCAL_DB_PATH em arquivo,
      não `:memory:`) + `BACKOFFICE_URL`/`LICENSE_KEY` reais, e passar pelo setup wizard via
      API antes dos testes (mais fiel a produção, mas perde o bypass de rate limit de login
      e o seed automático de usuários que a suíte atual depende — precisaria de um
      script de seed equivalente rodando via API depois do setup, não via `TEST_MODE` seed);
  (b) introduzir uma flag nova e explicitamente aditiva (`TEST_MODE_WITH_SYNC=true`) que
      preserve o `:memory:`+seed automático do `TEST_MODE` atual mas NÃO pule o bloco de
      bootstrap de cloud — exige separar a condição `!cfg.TestMode && cfg.BackofficeURL != ""`
      em duas flags independentes no `Config` (ex.: `SkipCloudBootstrap bool`, default
      `= TestMode`, mas sobrescrevível). Rota (b) é estritamente aditiva e de menor risco de
      regressão em `CROSS-004`/`GATEWAY-AVULSA-03`/`05` — é a que eu recomendaria para
      CROSS-017.0 continuar, mas não implementei porque mexer em `bootstrap.go`/`config.go`
      sem testar a suíte inteira depois é risco real demais para decidir sozinho nesta rodada;
      fica registrado aqui para o Tech Lead confirmar antes de alguém tocar nesses arquivos.
- **Não** criei `playwright.config.sync.ts` nem mexi em `syncro_desktop_client/frontend/e2e/`
  — sem a decisão acima resolvida, um webServer novo não teria o que subir do lado gateway.
- **Não** criei seed de 3 personas (admin/recep/médico) via setup wizard real — só confirmei
  o provisionamento de clínica. Depende da decisão (a)/(b) acima primeiro.
- Achado menor: `django-admin`/`manage.py shell` neste ambiente usa `Clinic.objects.create()`
  direto (não existe management command de provisionamento) — se uma sub-task futura quiser
  automatizar o seed de clínica de teste, dá pra escrever um `manage.py` command simples
  em cima do mesmo padrão usado aqui (`slug`/`cnpj` únicos, senão colide com dados antigos).

### Rodada 2 (2026-07-17) — gateway real subiu com `TEST_MODE_WITH_SYNC=true`, mas bloqueio novo encontrado

O Tech Lead implementou a flag `TEST_MODE_WITH_SYNC` (rota (b) recomendada acima) —
`config.go`/`bootstrap.go` foram ajustados de forma aditiva, sem regressão pro
`TEST_MODE` puro do CROSS-004 (confirmado lendo o diff: a condição virou
`if (!cfg.TestMode || cfg.TestModeWithSync) && cfg.BackofficeURL != ""` em
`bootstrap.go:447`).

**Passo a passo desta rodada:**

1. `docker compose up -d` (Postgres+Redis já estavam healthy de rodadas anteriores).
2. Backoffice Django local: `python manage.py runserver 8000` (checado antes com
   `manage.py check` — limpo).
3. Criada uma clínica de teste via shell (`Clinic.objects.create(slug='clinic-cross017-e2e', ...)`)
   → `provisioning_status=waiting_key`, `license_key` gerada automaticamente (UUID).
4. Subido o gateway Go **de verdade** (não script/curl manual):
   `TEST_MODE=true TEST_MODE_WITH_SYNC=true BACKOFFICE_URL=http://localhost:8000 LICENSE_KEY=<uuid da clínica> LISTEN_ADDR=0.0.0.0:8081 go run ./cmd/gateway -test-mode`.
5. Gateway subiu limpo (todas as migrations, 383 handlers, servidor ativo em `0.0.0.0:8081`),
   sem nenhum erro de crash — mas logou:
   `[license] banco não provisionado (status: waiting_key) — rode o setup primeiro`.

**Achado técnico (bloqueador para fechar CROSS-017.0 de ponta a ponta):**

`bootstrapCloudWithLicense` (`syncro_gateway/cmd/gateway/bootstrap.go:679`) **nunca chama
`RegisterPublicKey`** — ele só chama `lc.ValidateLicense(ctx)` (que bate em
`GET .../validate-license` ou equivalente) e, se `ProvisioningStatus != "provisioned"`,
desiste e loga a mensagem acima. `RegisterPublicKey` (o método que faz o
`POST /api/clinics/register-key/`, em
`syncro_gateway/internal/adapters/output/backoffice/license_client.go:126-149`) só é
chamado nos arquivos de teste (`bootstrap_license_test.go`, `license_client_test.go`,
`health_worker_test.go`) — confirmado com
`grep -rln "RegisterPublicKey" --include="*.go" .`: zero ocorrências fora de `_test.go`
e da própria definição do método. Não existe, hoje, nenhum caminho de produção (boot do
gateway ou `setup.Service.Initialize()` em
`syncro_gateway/internal/core/services/setup/service.go`) que registre a chave pública
RSA no backoffice — o par RSA é gerado e persistido localmente
(`edge_config.rsa_private_key_pem`), mas nunca sai do gateway.

Confirmado no lado backoffice depois do boot: `Clinic.objects.get(slug='clinic-cross017-e2e')`
→ `public_key_pem` vazio, `provisioning_status` continua `waiting_key`. Ou seja, o smoke
test manual do Tech Lead (401 com license key fake) provou só que a chamada HTTP de
`ValidateLicense` sai e chega no backoffice — não prova (e não podia provar, dado este
gap) que o fluxo de registro de chave funciona ponta a ponta, porque esse fluxo não é
disparado por lugar nenhum do código de produção.

**O que falta para fechar CROSS-017.0 de verdade:**

Alguém (Tech Lead decide onde) precisa decidir e implementar ONDE `RegisterPublicKey`
deveria ser chamado — os dois candidatos óbvios lendo o código atual:
- dentro de `setup.Service.Initialize()` (`internal/core/services/setup/service.go`),
  já que é lá que o par RSA é gerado — faria sentido registrar a chave pública no mesmo
  passo que a persiste localmente, se `cfg.BackofficeURL`/`LicenseKey` estiverem setados;
- ou como um passo prévio dentro do próprio `bootstrapCloudWithLicense`, antes do
  `ValidateLicense`, condicionado a algum estado ainda não registrado.

Sem essa decisão, o loop gateway→backoffice nunca vai passar de `waiting_key` em nenhum
ambiente real (não é specific do Docker local — é lacuna de código). Recomendo isso virar
uma task própria (ex. `CROSS-017.0b`) antes de tentar fechar esta de vez.

**Cleanup desta rodada:** gateway (`go run ./cmd/gateway`, porta 8081) e backoffice
(`runserver 8000`) finalizados via `kill`; clínica `clinic-cross017-e2e` deletada do
Postgres via shell (como `provisioning_status` nunca saiu de `waiting_key`, nenhum banco
de clínica chegou a ser provisionado — não havia nada para `deprovision_clinic_database`
limpar). Postgres/Redis do compose deixados rodando (já estavam de pé antes desta rodada).

### Rodada 3 (2026-07-17) — CROSS-017.0 FECHADO: loop completo confirmado ponta a ponta

Bug encontrado na Rodada 2 (`RegisterPublicKey` nunca era chamado em nenhum caminho de
produção) foi corrigido em `EDGW-042` (ver `GATEWAY-TASKS.md` → `FECHADAS.md`). Antes de
repetir o smoke test, confirmei a implementação lendo `bootstrapCloudWithLicense`
(`syncro_gateway/cmd/gateway/bootstrap.go:695-712`): agora, quando
`info.ProvisioningStatus == "waiting_key"`, lê `edge_config.rsa_public_key_pem` do SQLite
local e chama `lc.RegisterPublicKey(ctx, pubKeyPEM)` **antes** de checar se o status é
`provisioned` — condicional a `waiting_key` para não reenviar em boots subsequentes de
clínica já provisionada. Rodei os 3 testes de regressão Go que cobrem isso
(`go test ./cmd/gateway/... -run TestBootstrap -v`, dentro de `syncro_gateway/`):
`TestBootstrap_WaitingKey_RegistersPublicKeyOnce`,
`TestBootstrap_Provisioned_NeverRegistersPublicKey`,
`TestBootstrap_WaitingKey_RegisterPublicKeyFailure_DoesNotCrash` — os 3 passando
(`ok syncro_gateway/cmd/gateway 0.259s`).

**Passo a passo desta rodada (repete exatamente a Rodada 2, mesma infra):**

1. Postgres+Redis do compose já `healthy` de rodadas anteriores (`docker compose ps`).
2. Backoffice Django local: `python manage.py check` limpo, depois
   `python manage.py runserver 8000` em background.
3. Clínica nova criada via shell (evitar reusar a da Rodada 2, que ficou presa em
   `waiting_key`):
   ```python
   Clinic.objects.create(name='Clinica CROSS017 Rodada3', slug='clinic-cross017-r3', cnpj='11222333000181')
   # → license_key=3dc54d43-bb21-4bff-8464-da667086bee9, provisioning_status=waiting_key, public_key_pem vazio
   ```
4. Gateway Go subido de verdade (não script/curl manual), mesmos flags da Rodada 2:
   ```
   TEST_MODE=true TEST_MODE_WITH_SYNC=true BACKOFFICE_URL=http://localhost:8000 \
   LICENSE_KEY=3dc54d43-bb21-4bff-8464-da667086bee9 LISTEN_ADDR=0.0.0.0:8081 \
   go run ./cmd/gateway -test-mode
   ```
5. Boot limpo, todas as migrations aplicadas, servidor ativo — e desta vez o log mostra
   as DUAS linhas esperadas em sequência:
   ```
   [license] chave pública registrada com sucesso no backoffice
   [license] banco não provisionado (status: waiting_key) — rode o setup primeiro
   ```
   A segunda linha é esperada e correta: `ValidateLicense` foi chamado ANTES do registro
   da chave nesse mesmo boot, então o `info.ProvisioningStatus` em memória, usado na
   checagem seguinte, ainda reflete o estado pré-registro (`waiting_key`) — o registro
   dispara o signal do lado Django de forma assíncrona ao ciclo de vida desse processo
   Go. O provisionamento real acontece no backoffice, não no gateway; confirmar o
   avanço de estado é responsabilidade do PRÓXIMO passo (verificação direta no Django/Postgres),
   não deste mesmo boot.

**Confirmação do lado backoffice (log de acesso + shell):**

```
[17/Jul/2026 12:31:58] "POST /api/clinics/validate-license/ HTTP/1.1" 200 150
[17/Jul/2026 12:31:58] "POST /api/clinics/register-key/ HTTP/1.1" 200 37
```

```python
c = Clinic.objects.get(slug='clinic-cross017-r3')
c.provisioning_status   # → 'provisioned'  (era 'waiting_key')
bool(c.public_key_pem)  # → True (450 chars, era vazio)
c.db_name                # → 'clinic_clinic_cross017_r3'
c.db_user                # → 'u_clinic_cross017_r3'
bool(c.db_password_encrypted)  # → True
```

O `POST /api/clinics/register-key/` disparou o signal `post_save` de
`clinics/signals.py`, que chamou `provisioning.py::provision_clinic_database()` de
verdade — confirmado com:

```bash
docker exec syncro-backoffice-postgres-1 psql -U superuser -d clinic_clinic_cross017_r3 -c '\dt'
```

Resultado: 20 tabelas do schema completo (`patients`, `appointments`, `professionals`,
`schedules`, `schedule_availability`, `clinic_settings`, `edge_registry`,
`insurance_operators`, `audit_log`, etc.), todas de propriedade de
`clinic_template_owner` — o mesmo padrão de isolamento já validado em rodadas
anteriores. ACL confirmado: `\dp patients` mostra `Access privileges` vazio (sem
`PUBLIC`), ou seja, `REVOKE ALL ... FROM PUBLIC` segue em vigor.

**Resultado: CROSS-017.0 fechado de ponta a ponta.** O loop completo —
`waiting_key` → registro de chave pública real via gateway → signal Django →
`provision_clinic_database()` real → `provisioned` com schema aplicado no Postgres —
funciona sem workaround, sem curl manual, sem bypass. É exatamente o caminho que uma
clínica nova percorre em produção no primeiro boot do gateway.

**Cleanup desta rodada:**
- Gateway (`go run ./cmd/gateway`, PID do processo `go build` filho, porta 8081) morto
  via `kill` no PID do binário compilado (o PID do `go run` em si não é o do processo
  que escuta a porta — confirmado com `lsof -i :8081` antes/depois de matar cada um).
- Backoffice (`runserver 8000`) finalizado via `kill`; `curl` pós-kill confirma
  conexão recusada.
- Banco da clínica removido de verdade via
  `clinics/provisioning.py::deprovision_clinic_database(db_name, db_user)` (assinatura
  recebe `db_name`/`db_user`, não a instância) e o registro `Clinic` deletado do SQLite
  do backoffice. `\l` no Postgres pós-cleanup confirma que `clinic_clinic_cross017_r3`
  não existe mais.
- Postgres/Redis do compose deixados rodando (infra persistente entre rodadas, como nas
  anteriores).

### O que ficou pendente

Nada bloqueando CROSS-017.0 — está fechado. O item registrado como pendente nas rodadas
anteriores (CROSS-017.1 — specs Playwright/`playwright.config.sync.ts` cobrindo telas
reais contra este ambiente sincronizado) segue fora do escopo desta sub-task, aguardando
priorização do Tech Lead antes de começar, como combinado.

## BACFF-AVULSA-06 — validação QA ponta a ponta do grant temporário de acesso ao Postgres da clínica (2026-07-17)

Reaproveita a mesma infra validada em `CROSS-017.0` (Postgres+Redis do compose, backoffice
Django local, gateway real com `TEST_MODE_WITH_SYNC=true`). Objetivo: provar, sem mock em
nenhuma ponta, o fluxo completo `admin autoriza no gateway → gateway decripta a senha real
do Postgres da clínica → envia ao Backoffice com TTL → Backoffice guarda em cache Redis →
`clinic_db_connection()` conecta como usuário escopado da clínica só durante a janela do
grant`.

### Passo a passo executado

1. Postgres+Redis do compose já `healthy` de rodadas anteriores (`docker compose ps`).
2. Backoffice Django local, **com `CACHE_URL=redis://localhost:6379/1` explícito**
   (`python manage.py runserver 8000`) — ver achado abaixo sobre por que isso é
   obrigatório para validação externa, não só para produção multi-worker.
3. Duas clínicas de teste criadas via shell (`Clinic.objects.create(...)`) para cobrir o
   cenário multi-tenant do passo 7: `clinic-bacff06-qa` e `clinic-bacff06-qa-2`, ambas
   `provisioning_status=waiting_key` na criação.
4. Dois gateways Go reais subidos em paralelo, um por clínica (portas `8081`/`8082`),
   mesmo padrão de `TEST_MODE=true TEST_MODE_WITH_SYNC=true BACKOFFICE_URL=http://localhost:8000
   LICENSE_KEY=<uuid da clínica> go run ./cmd/gateway -test-mode` do `CROSS-017.0`. Ambos
   registraram a chave pública e avançaram para `provisioned` (confirmado via shell Django:
   `db_name`, `db_user`, `db_password_encrypted` populados para as duas).
5. Login real como `admin@test.syncro` (seed do `TEST_MODE`) em cada gateway via
   `POST /api/v1/auth/login`, obtendo JWT real.
6. `POST /api/v1/db-access-grant` (autenticado, role admin) em cada gateway →
   `200 {"granted": true, "expires_at": "..."}` nos dois.
7. Confirmado no Redis real (`docker exec ... redis-cli -n 1 --scan --pattern
   "*clinic_db_grant*"`): duas chaves distintas, uma por `clinic.id`
   (`clinic_db_grant:<uuid>`), **sem colisão** — cenário de borda do passo 7 do pedido
   original, validado explicitamente com duas clínicas simultâneas.
8. `TTL` da chave no Redis confere com o `ttl_seconds` enviado (60s pedido → `TTL` lido em
   56s, consistente com o tempo decorrido entre grant e checagem). Valor armazenado
   (`redis-cli GET`, pickled) contém `password`, `granted_by`, `granted_at`, `expires_at` —
   exatamente o formato do contrato.
9. **Dentro da janela do grant**, chamada real a `clinic_db_connection(clinic)` via shell
   Django (`with clinic_db_connection(c) as conn: cur.execute('SELECT current_user,
   current_database()')`) — resultado: `('u_clinic_bacff06_qa', 'clinic_clinic_bacff06_qa')`
   para a clínica 1 e `('u_clinic_bacff06_qa_2', 'clinic_clinic_bacff06_qa_2')` para a
   clínica 2. Confirma que a conexão usa o **usuário escopado da própria clínica**, não
   mais `PROVISIONING_DATABASE_URL`/superusuário, e que não há vazamento cruzado entre
   as duas clínicas testadas simultaneamente.
10. **Sem grant** (`redis-cli DEL` na chave, simulando expiração sem esperar o TTL real):
    a mesma chamada a `clinic_db_connection(clinic)` levanta
    `django.core.exceptions.PermissionDenied: Acesso ao banco da clínica não autorizado
    no momento — peça à clínica para liberar via gateway local.` — **sem fallback
    silencioso para superuser**, confirmado lendo o resultado real do shell (não é
    inferência de código).

### Achado durante a validação (não bloqueante, mas real) — path do endpoint

Primeira tentativa de chamar o endpoint usou `POST /admin/db-access-grant` (lido
apressadamente da entrada `## 2. Contrato do lado gateway` da task, que descreve a rota
como `POST /api/settings/db-access-grant` em prosa, mas o handler implementado registra
em `internal/adapters/input/rest/admin/db_access_handler.go:32`
(`admin.Post("/db-access-grant", ...)`) dentro do grupo `adminOnly` já prefixado por
`/api/v1` em `cmd/gateway/routes.go:308` — o parâmetro chamado `admin` na assinatura de
`SetupRoutes(admin fiber.Router)` é só o nome da variável recebida, não um prefixo de URL).
Path real e correto: **`POST /api/v1/db-access-grant`** (sem `/admin`). Confirmado batendo
nesse path com JWT de admin real → `200`. Não é bug — é imprecisão de path na
documentação da task/contrato, que vale corrigir para não repetir a confusão numa
próxima rodada (ex. ao escrever a spec E2E do frontend).

### Achado durante a validação — `CACHE_URL` precisa estar setado para o teste (e é o comportamento correto)

Primeira tentativa de verificar o grant via `manage.py shell` num processo separado do
`runserver` retornou `cache.get(...) == None`, mesmo o gateway tendo recebido `200` do
endpoint. Causa: sem `CACHE_URL` setado, `syncro_backoffice/settings.py` cai em
`CACHES = {'default': {'BACKEND': '...locmem.LocMemCache'}}` — cache por-processo, que o
próprio comentário do settings já documenta como intencional para dev local sem infra.
Como o `runserver` e o `shell` são processos distintos, o `shell` nunca veria o que o
`runserver` gravou. Corrigido subindo o backoffice com `CACHE_URL=redis://localhost:6379/1`
explícito (mesmo DB do Celery broker, índice 1, como o comentário do settings já indica) —
depois disso, o grant apareceu corretamente no Redis real e pôde ser inspecionado
externamente. **Não é bug**: é exatamente o guard-rail que o próprio settings já implementa
(`raise RuntimeError` se `DEBUG=False` sem `CACHE_URL`) — só reforça que qualquer ambiente
que precise de cache compartilhado entre processos (incluindo validação QA externa, não só
produção multi-worker) precisa setar `CACHE_URL` explicitamente.

### Cleanup desta rodada

- Os dois gateways (`go run ./cmd/gateway`, portas 8081/8082) finalizados via `kill` nos
  PIDs reais que escutavam a porta (confirmado com `lsof -i` antes/depois).
- Backoffice (`runserver 8000`) finalizado via `kill`.
- Bancos das duas clínicas de teste removidos de verdade via
  `clinics/provisioning.py::deprovision_clinic_database(db_name, db_user)`; registros
  `Clinic` deletados do SQLite do backoffice; `\l` no Postgres pós-cleanup confirma que
  nenhum `clinic_clinic_bacff06_qa*` restou.
- Chaves `clinic_db_grant:*` removidas do Redis (`redis-cli DEL`), sem restos.
- Postgres/Redis do compose deixados rodando (infra persistente entre rodadas, como nas
  anteriores).

### Resultado

**Fechado de ponta a ponta, sem mock em nenhuma etapa.** Os 7 pontos pedidos na validação
foram confirmados com evidência real (JWT real, chamada HTTP real, Redis real, conexão
Postgres real com `current_user`/`current_database()` lidos do próprio banco, dois
tenants simultâneos sem colisão de chave). Nenhum bloqueio real encontrado — os dois
achados acima são desvios de documentação/ambiente, não bugs de código, e já foram
contornados e documentados para não se repetirem.
