# ADMIN-DASHBOARD-REDESIGN — Reorganização do Backoffice + Dashboard de Saúde de Serviços

**type:** design / pesquisa + implementação parcial (tasks 1-3 do §6 concluídas)
**priority:** P1
**detected_by:** Tech Lead (2026-07-28) — "tela de admin extremamente bagunçada de tantas funcionalidades"
**status:** parcialmente implementado — ver "IMPLEMENTAÇÃO (rodada 2026-07-28)" logo abaixo. Itens com ⚠️ no corpo do documento continuam aguardando decisão do Tech Lead.
**escopo:** repo `syncro-backoffice` (Django 6.0.7 + django-unfold 0.99), branch base `develop`, PR da implementação: `feat/admin-dashboard-reorg`

---

## IMPLEMENTAÇÃO (rodada 2026-07-28)

Executado exatamente o que o §6 marcou como "✅ pode começar" (não depende
de nenhuma decisão pendente do Tech Lead):

| # | Task do §6 | Status |
|---|---|---|
| 1 | Reorganizar `UNFOLD["SIDEBAR"]` em 5 grupos (Principal/Administrar/Operações/Configurações/Sistema), remover `TABS` morto | ✅ feito |
| 2 | Registrar `InvoiceAdmin` + `PlanAdmin` | ✅ feito |
| 3 | `DASHBOARD_CALLBACK` com os 3 cards de dado real (Clientes ativos/inativos, Gateways online, Erros 24h) | ✅ feito |
| 4 | Card de inadimplentes | ⏸ bloqueado por decisão §5.1 (estado `OVERDUE`) — não implementado |
| 5 | `tiss.OperatorCallLog` + instrumentação do `soap_client` + card por operadora | ⏸ não incluído nesta rodada — ver nota abaixo |
| 6 | Card de banco | ⏸ bloqueado por decisão §4.2 — não implementado |
| 7-9 | CBO/CID-10, Evolution API, custo de API | ⏸ fora de escopo, conforme o documento já registrava |

**Arquivos alterados:**
- `syncro_backoffice/settings.py` — `UNFOLD["SIDEBAR"]["navigation"]` com os
  5 grupos e todos os 20 ModelAdmins (exceto os 2 intencionalmente fora do
  menu: `TicketMessage` inline e `ClinicUserNoticeDismissal` telemetria);
  `UNFOLD["DASHBOARD_CALLBACK"]` apontando pro novo módulo; `COMMAND.search_models`
  ganhou `billing.Invoice` e `accounts.ClinicUser`; bloco `UNFOLD["TABS"]`
  morto (apontava pra `clinicas.clinica`, app/model inexistente) removido.
- `syncro_backoffice/dashboard.py` (novo) — `dashboard_callback`, com
  isolamento por `ClinicAccess` replicando `TenantScopedAdminMixin` (o
  callback roda fora de qualquer `ModelAdmin`) e cache de 60s por usuário.
- `templates/admin/index.html` (novo, `DIRS` do `TEMPLATES` ajustado em
  `settings.py`) — estende `admin/base.html` (não `admin/index.html`, evita
  recursão), com os 3 cards gateados por `{% if perms.<app>.<perm> %}` pra
  não vazar contagem/rótulo pra quem não tem a permissão correspondente.
- `billing/admin.py` — `PlanAdmin` (catálogo global, sem tenant scoping) e
  `InvoiceAdmin` (`TenantScopedAdminMixin`, `clinic_lookup='clinic'` default).
- Testes novos: `syncro_backoffice/tests_dashboard.py`, `billing/tests_admin.py`,
  mais uma classe `AdminSidebarReorgTest` em `accounts/tests_admin_groups.py`.
  Suite completa: 646 testes, 100% verde (`python manage.py test`).

**Decisão sobre a task 5 (log de chamadas por operadora TISS):** o
documento classificou como "quase pronto, ~3h extra". Não incluí nesta
rodada porque instrumentar `tiss/soap_client.py` corretamente (medir
`time.perf_counter()` ao redor de duas chamadas diferentes — elegibilidade
e envio de lote —, mais a migration do `OperatorCallLog`, mais a
classificação de estado do §4.1, mais o comando de purga de 90 dias) é
naturalmente maior que as tasks 1-3 e eu não queria arriscar entregar
instrumentação incompleta/errada no mesmo PR das tasks sem decisão
pendente. Fica registrado como próximo passo natural — o desenho já está
pronto no §4.1 deste documento, só falta o código.

---

## 0. TL;DR — vereditos

| Pedido | Veredito |
|---|---|
| Menu "Configurações" (feriados, operadoras, CBO, CID…) | ✅ **viável agora** — é config de `UNFOLD["SIDEBAR"]`, ~1h. Ressalva: CBO/CID **não existem neste repo**. |
| Menu "Administrar → Clientes" (listagem) | ✅ **já existe** (`clinics.Clinic`), só muda de lugar no menu |
| Mini-dashboard ativos/inadimplentes/inativos | ⚠️ **precisa de decisão sua** — "inadimplente" não é um estado modelado hoje (ver §5) |
| Dashboard: saúde por operadora TISS | 🟡 **viável com tabela nova** — dados de latência/erro **não são persistidos hoje** |
| Dashboard: saúde de banco (conexões/memória/query) | ⚠️ **precisa de decisão** — ambiente atual é Railway/Postgres gerenciado; `pg_stat_statements` não confirmado |
| Dashboard: monitoramento Evolution API | 🔴 **bloqueado por infra** — nenhuma linha de Python fala com o Evolution hoje; ele só existe no `docker-compose.prod.yml` |
| Dashboard: custo de APIs | 🔴 **não existe nada**. E hoje, na prática, **não há API paga por chamada** (ver §4.4) |

---

## 1. Achado nº1 (o mais importante): o problema não é o que parece

A hipótese do enunciado era "lista grande de serviços/telas sem organização".
**Não é isso.** O `UNFOLD["SIDEBAR"]` já está curado desde a TASK-057
(`settings.py:251-324`), com `"show_all_applications": False` e apenas
**8 links** em 3 grupos (Principal / Operações / Sistema).

O problema real é o inverso: **20 ModelAdmins estão registrados e apenas 8
estão alcançáveis pelo menu.** Os outros 12 existem, são navegáveis por URL
direta (`/admin/tiss/tissguia/`), respeitam permissão — mas **nenhum humano
os encontra pelo menu**. A sensação de "bagunça" vem de o menu não bater com
o sistema: você sabe que a tela de feriados existe, ela não está no menu,
então você procura, não acha, e conclui que está tudo bagunçado.

Corolário: a reorganização pedida **é a solução certa**, mas o trabalho não é
"tirar coisa do menu" — é **colocar as 12 telas órfãs em grupos que façam
sentido**.

### 1.1 Inventário completo — 20 ModelAdmins registrados

| # | Model | Arquivo | No menu hoje? | Categoria proposta |
|---|---|---|---|---|
| 1 | `clinics.Clinic` | `clinics/admin.py:7` | ✅ "Clínicas" | **Administrar → Clientes** |
| 2 | `accounts.SupportUser` | `accounts/admin.py:11` | ❌ órfã | Sistema → Usuários & Permissões |
| 3 | `accounts.ClinicAccess` | `accounts/admin.py:28` | ❌ órfã | Sistema → Acessos de suporte |
| 4 | `accounts.ClinicUser` | `accounts/admin.py:51` | ❌ órfã | Administrar → Usuários do cliente |
| 5 | `metrics.SystemHeartbeat` | `metrics/admin.py:12` | ✅ "Gateways" | Operações → Gateways |
| 6 | `metrics.SystemLog` | `metrics/admin.py:61` | ✅ "Logs" | Operações → Logs |
| 7 | `support.Ticket` | `support/admin.py:16` | ✅ "Suporte" | Operações → Suporte |
| 8 | `support.TicketMessage` | `support/admin.py:133` | ❌ órfã | (inline do Ticket — **não pôr no menu**) |
| 9 | `portal_gestor.ReportSession` | `portal_gestor/admin.py:8` | ✅ "Relatórios" | Operações → Relatórios |
| 10 | `portal_gestor.PortalReadAuditLog` | `portal_gestor/admin.py:36` | ❌ órfã | Sistema → Auditoria de leitura (LGPD) |
| 11 | `portal_gestor.ProductNotice` | `portal_gestor/admin.py:60` | ❌ órfã | Operações → Avisos de produto |
| 12 | `portal_gestor.ClinicUserNoticeDismissal` | `portal_gestor/admin.py:77` | ❌ órfã | (telemetria — **não pôr no menu**) |
| 13 | `tiss.TISSOperatorConfig` | `tiss/admin.py:14` | ❌ órfã | **Configurações → Operadoras (credenciais)** |
| 14 | `tiss.TISSLote` | `tiss/admin.py:26` | ❌ órfã | Operações → Faturamento TISS |
| 15 | `tiss.TISSGuia` | `tiss/admin.py:63` | ❌ órfã | Operações → Faturamento TISS |
| 16 | `tiss.TISSGlosa` | `tiss/admin.py:114` | ❌ órfã | Operações → Faturamento TISS |
| 17 | `tiss.TISSElegibilidadeConsulta` | `tiss/admin.py:125` | ❌ órfã | Operações → Elegibilidade (é o log de chamadas) |
| 18 | `tiss.TUSSProcedureCode` | `tiss/admin.py:142` | ❌ órfã | **Configurações → Tabela TUSS** |
| 19 | `tiss.ANSInsuranceOperator` | `tiss/admin.py:152` | ❌ órfã | **Configurações → Operadoras ANS** |
| 20 | `municipios.Municipio` | `municipios/admin.py:7` | ❌ órfã | **Configurações → Municípios (IBGE)** |
| 21 | `holidays.Feriado` | `holidays/admin.py:5` | ❌ órfã | **Configurações → Feriados** |
| — | `auth.Group` / `auth.User` | Django | ✅ "Usuários & Permissões" | Sistema |

### 1.2 Achados colaterais do inventário

- 🔴 **`billing.Plan` e `billing.Invoice` NÃO estão registrados no admin.**
  `billing/admin.py` tem 3 linhas — é o arquivo gerado pelo `startapp`, nunca
  tocado. Existem models, serializers, viewsets REST (`billing/views.py`) e
  webhook Asaas funcionando, mas **zero tela**. Ninguém consegue ver uma
  fatura pelo admin hoje. Isso é diretamente relevante para o pedido nº3.
- 🔴 **`UNFOLD["TABS"]` (`settings.py:325-339`) está quebrado/morto.**
  Aponta para `"clinicas.clinica"` e `admin:clinics_clinica_changelist` — o
  app label real é `clinics` e o model é `Clinic`, então `clinics_clinica_…`
  não existe. O comentário no código ("Chave obrigatória que corrige o
  KeyError") indica que isso foi um workaround de um erro, não uma feature
  desejada. **Recomendo remover o bloco inteiro** nesta task.
- 🟡 **App `integrations` está vazio** (`models.py` só com o comentário do
  `startapp`, sem migrations, sem views) mas está em `INSTALLED_APPS`. É o
  lugar natural para o health-check de terceiros desenhado no §4 — ou deve
  ser removido.
- 🟡 **Dependências instaladas e não usadas:** `django-tenants==3.10.1`
  (não está em `INSTALLED_APPS`, isolamento é feito por
  `TenantScopedAdminMixin` manual), `django-admin-charts` + `django-nvd3` +
  `python-nvd3` + `django-bower` (nenhum em `INSTALLED_APPS`),
  `django-memoize`, `django-multiselectfield`, `notion-client` (Notion foi
  substituído por Zoho Desk — `settings.py:342`). ⚠️ `django-admin-charts`
  seria justamente a lib de gráfico do dashboard — ou usa, ou remove.
- ℹ️ `UNFOLD["ENVIRONMENT"]` está fixo em `"staging"` — a produção mostra
  a tarja amarela "Staging". Cosmético, mas confunde.

---

## 2. O que já existe de métricas / health-check (levantamento, não suposição)

Grep por `health|latency|uptime|pg_stat|status_check` em todo o repo:

| Existe | Onde | Serve pro dashboard? |
|---|---|---|
| `GET /health/` — liveness puro | `syncro_backoffice/urls.py:7` | Não. É deliberadamente burro: não toca banco nem Redis (comentário no código explica que é liveness, não readiness). |
| `metrics.SystemHeartbeat` | `metrics/models.py:4` | **Sim, e é o melhor ativo que temos.** Um registro por clínica com `gateway_version`, `db_size_mb`, `pending_sync`, `sync_connected`, `last_seen`. É a saúde do **gateway da clínica**, não dos nossos serviços. |
| `LastSeenFilter` | `metrics/admin.py:45` | Sim — já classifica gateway como online/atrasado por `last_seen`. **É exatamente o padrão a replicar por operadora TISS.** |
| `metrics.SystemLog` | `metrics/models.py:33` | Parcialmente — logs por clínica com `level`, dá pra derivar taxa de erro. |
| `tiss.TISSElegibilidadeConsulta` | `tiss/models.py:245` | **Sim, parcialmente** — tem `operator_config` (FK), `status`, `erro_mensagem`, `created_at`. É o único registro persistido de chamada a operadora. **Não tem latência.** |
| Healthchecks Docker | `docker-compose.prod.yml` | Só do orquestrador, não expostos ao Django. |

**Não existe:** nenhuma métrica de infra nossa (CPU/memória/conexões), nenhum
tracking de custo, nenhuma integração com ferramenta de observabilidade
(sem Sentry, sem `django-health-check`, sem `psutil` no `requirements.txt`),
nenhum código que fale com o Evolution API.

---

## 3. Estrutura de menu proposta

```
┌ PRINCIPAL
│   Dashboard de Serviços          ← NOVO, vira a home do /admin/ (§4)
│
├ ADMINISTRAR
│   Clientes                       ← clinics.Clinic + mini-dashboard (§5)
│   Usuários do cliente            ← accounts.ClinicUser
│   Faturas                        ← billing.Invoice  (PRECISA registrar admin)
│   Planos                         ← billing.Plan     (PRECISA registrar admin)
│
├ OPERAÇÕES
│   Gateways                       ← metrics.SystemHeartbeat
│   Faturamento TISS
│     ├ Lotes                      ← tiss.TISSLote
│     ├ Guias                      ← tiss.TISSGuia
│     └ Glosas                     ← tiss.TISSGlosa
│   Elegibilidade                  ← tiss.TISSElegibilidadeConsulta
│   Relatórios                     ← portal_gestor.ReportSession
│   Suporte                        ← support.Ticket
│   Avisos de produto              ← portal_gestor.ProductNotice
│
├ CONFIGURAÇÕES                    ← NOVO GRUPO (pedido nº2)
│   Feriados                       ← holidays.Feriado
│   Operadoras ANS                 ← tiss.ANSInsuranceOperator
│   Credenciais de operadora       ← tiss.TISSOperatorConfig  ⚠️ ver nota
│   Tabela TUSS                    ← tiss.TUSSProcedureCode
│   Municípios (IBGE)              ← municipios.Municipio
│   [CBO]                          ← ❌ NÃO EXISTE neste repo
│   [CID-10]                       ← ❌ NÃO EXISTE neste repo
│
└ SISTEMA
    Logs                           ← metrics.SystemLog
    Auditoria de leitura (LGPD)    ← portal_gestor.PortalReadAuditLog
    Acessos de suporte             ← accounts.ClinicAccess
    Usuários & Permissões          ← auth.Group + accounts.SupportUser
```

Fora do menu de propósito: `support.TicketMessage` (já é inline do Ticket) e
`portal_gestor.ClinicUserNoticeDismissal` (telemetria, ruído).

### 3.1 ⚠️ CBO e CID-10 não existem neste repositório

Isso é uma correção direta à premissa do pedido. Grep por `CBO` no backoffice
retorna **uma única ocorrência**, num comentário de `tiss/edge_client.py:54`
("CBO, CID10, carteirinha etc. já preenchidos pelo Edge"). CID-10: zero
ocorrências. Não há model, migration, seed ou fixture.

Ou seja: **CBO e CID-10 são hoje dados do Edge Gateway (repo `syncro_health`),
não do Backoffice.** Colocá-los em Configurações é uma feature nova — replicar
o padrão de tabela de referência distribuída que já existe para TUSS/ANS
(`TUSSProcedureCode.updated_at` com o comentário "usado pelo gateway para
invalidar cache local"). É viável, é ~4-6h por tabela (model + import de
fonte oficial + comando de seed + admin readonly), mas **não é reorganização
de menu, é feature**. Recomendo tratar como tasks separadas
(`CFG-001-cbo`, `CFG-002-cid10`) e não bloquear a reorganização por elas.

### 3.2 ⚠️ Duas telas que eu não sei onde encaixar — decida você

1. **`tiss.TISSOperatorConfig`** — é config (endpoint, provider) **e** é dado
   por cliente (tem FK para `Clinic`, guarda `login_encrypted`/`senha_encrypted`
   da clínica). Coloquei em Configurações, mas o argumento pra ficar em
   Administrar → Clientes (como inline da clínica) é igualmente forte, e
   provavelmente melhor em termos de fluxo: quando você configura um cliente
   novo, você quer as credenciais TISS dele ali, não num cadastro global.
   **Não force — me diga qual fluxo você usa.**
2. **`portal_gestor.ProductNotice`** — é comunicação de produto (marketing/CS),
   não é nem operação nem configuração. Coloquei em Operações por falta de
   lugar melhor. Se surgir um grupo "Comunicação" no futuro, migra.

### 3.3 Como implementar (nota técnica, para quando virar código)

Tudo em `settings.py`, na lista `UNFOLD["SIDEBAR"]["navigation"]` — mesmo
padrão que já está lá: `title` + `icon` (Material Symbols) + `link`
(`reverse_lazy("admin:<app>_<model>_changelist")`) + `permission` (lambda).
O Unfold já esconde item sem permissão, **não** escrever lógica extra.
Para os sub-níveis (Faturamento TISS), o Unfold 0.99 suporta `"items"`
aninhados via `collapsible: True` no grupo — confirmar na versão pinada
antes de assumir. Se não suportar, achatar em itens de primeiro nível com
prefixo ("TISS · Lotes") — não vale subir versão do Unfold por causa disso.

**Custo:** ~1h de config + ajuste de `COMMAND.search_models` (hoje só 3
models; incluir `billing.Invoice` e `accounts.ClinicUser`). Sem migration.

---

## 4. Dashboard de Saúde de Serviços

Onde vive: substituir a index do `/admin/` via `UNFOLD["DASHBOARD_CALLBACK"]`
(hoje **não configurado** — a home é a index padrão do Django, que com
`show_all_applications: False` é praticamente inútil). Isso dá a tela inicial
pedida sem criar rota nem app novo.

### 4.1 Saúde das integrações TISS por operadora — 🟡 viável, precisa de tabela nova

**Como o sistema sabe hoje se a Orizon está de pé: ele não sabe.**
`tiss/soap_client.py` faz a chamada, loga erro (`logger.error`, linha 343/372)
e retorna `SOAPFaultResult`. Nada é persistido, não há medição de tempo, não há
contador. O único rastro em banco é `TISSElegibilidadeConsulta` — e só para
elegibilidade (envio de lote não gera registro equivalente), sem latência.

**Desenho proposto — genérico por operadora desde o primeiro dia** (o pedido
foi explícito em não hardcodar Orizon, e a modelagem já ajuda: a chave de
agregação é `registro_ans`, não o nome do gateway):

```
tiss.OperatorCallLog          (append-only, 1 linha por chamada SOAP)
  registro_ans      CharField(6)     ← chave genérica, NÃO "orizon"
  gateway_provider  CharField        ← reusa TISSGatewayProvider
  operation         CharField        ← elegibilidade | envio_lote | status_protocolo
  clinic            FK(Clinic, null=True)
  outcome           CharField        ← success | soap_fault | network_error | timeout
  latency_ms        PositiveInteger
  http_status       PositiveInteger(null)
  created_at        DateTime(db_index=True)
  # SEM payload, SEM erro_mensagem cru → LGPD: resposta TISS contém PHI
  index: (registro_ans, created_at)
```

Escrita: um único ponto, dentro de `tiss/soap_client.py`
(`verificar_elegibilidade` / `enviar_lote`), envolvendo a chamada com
`time.perf_counter()`. ~20 linhas. Não passar por Celery — a escrita é 1 INSERT
e o custo é irrelevante perto de uma chamada SOAP; enfileirar só adiciona
modo de falha.

**Classificação de estado** (janela deslizante de 15 min, por `registro_ans`):

| Estado | Regra |
|---|---|
| 🟢 Saudável | ≥1 chamada na janela **e** taxa de erro < 10% **e** p95 latência < 5s |
| 🟡 Degradado | taxa de erro 10–50%, **ou** p95 ≥ 5s |
| 🔴 Fora do ar | taxa de erro ≥ 50% com ≥3 chamadas na janela |
| ⚪ Sem tráfego | 0 chamadas na janela — **estado distinto, não "saudável"** |

⚠️ **Decisão sua:** "sem tráfego" é o estado normal da maior parte do dia
(volume real é baixo). Só há duas saídas: (a) aceitar ⚪ como cinza neutro e
alargar a janela para 24h; (b) fazer um **probe ativo** — uma task Celery Beat
chamando um método barato e idempotente da operadora a cada N minutos. (b) dá
um dashboard de verdade, mas exige saber qual operação é segura de chamar em
loop (a Orizon pode ter rate limit / contrato que cobra por consulta) e
credencial de uma clínica real — o que é ruim (usar credencial de cliente para
monitoramento nosso é problema de LGPD e de contrato). **Minha recomendação:
(a) no MVP.** Probe ativo só quando tivermos credencial de homologação própria.

Retenção: purgar `OperatorCallLog` > 90 dias (comando de management +
Celery Beat), senão a tabela cresce sem limite.

### 4.2 Saúde de banco de dados — ⚠️ precisa de decisão sua

Tecnicamente é fácil. Estrategicamente eu **não recomendo** construir no admin.

O que dá pra fazer com uma query crua, sem nenhuma extensão:
- **Conexões ativas:** `SELECT state, count(*) FROM pg_stat_activity WHERE datname = current_database() GROUP BY state` — disponível em qualquer Postgres, inclusive gerenciado.
- **Tamanho do banco:** `pg_database_size(current_database())`.
- **Cache hit ratio:** `pg_stat_database` (`blks_hit / (blks_hit + blks_read)`).
- **Transações mais antigas em aberto** (detecta lock/leak de conexão): `max(now() - xact_start)` em `pg_stat_activity`.

O que **não** dá sem infra adicional:
- **Performance de consulta (top queries lentas):** exige `pg_stat_statements`,
  que precisa estar em `shared_preload_libraries` no `postgresql.conf` e
  demanda `CREATE EXTENSION` — ou seja, **restart do servidor + superuser**.
  Não confirmei se está habilitado no ambiente atual, e não dá pra confirmar
  sem acesso ao Postgres de produção. Em Postgres gerenciado (Railway) isso
  costuma ser possível mas não é default.
- **"Consumo de memória":** o Postgres não expõe uso de RAM do processo por
  SQL. Isso é métrica de host (`psutil`/cAdvisor/painel do provider), não de
  banco. Se a intenção era "memória do container do backoffice", aí sim é
  `psutil` — dep nova, e no Railway a leitura de `/proc` dentro do container
  reporta o host, não o limite do container (leitura enganosa).

⚠️ **Decisão sua (a mais importante deste documento):** hoje o Django roda em
Railway (default de `DATABASE_URL`, `PROVISIONING_HOST`, e a task
`ARCH-CELERY-SPLIT-001` fala explicitamente do modelo de serviços do Railway).
O Railway já expõe CPU/memória/rede por serviço no painel dele.
**Reimplementar isso no Django admin é reinventar observabilidade num lugar
onde ela não pertence** — e, pior, a query em `pg_stat_activity` roda no
request do admin, então uma tela de monitoramento passa a **adicionar carga**
ao banco que ela monitora, e fica indisponível exatamente quando o banco cai
(que é quando você precisa dela).

Minha recomendação, em ordem:
1. **MVP:** só um card "Banco" com conexões ativas + tamanho + cache hit,
   cacheado 60s no Redis (que já existe), com o número **e** um link "ver
   detalhes no painel Railway". Sem gráfico histórico, sem `pg_stat_statements`.
2. **Se você quiser observabilidade de verdade:** Sentry (performance +
   erro, já cobre "query lenta" via tracing e é ~30min de setup) ou o
   painel do provider. **Não** um dashboard caseiro.

⚠️ Também precisa de decisão: **qual banco?** O sistema é multi-banco —
`clinics/provisioning.py` cria **um Postgres por clínica**. "Saúde do(s)
banco(s)" pode significar o banco do backoffice (1) ou os N bancos de clínica.
Monitorar N bancos de clínica exige N conexões por render da tela — inviável
no request; teria que virar task Celery periódica gravando snapshot. Assumi
**só o banco do backoffice** no MVP.

### 4.3 Monitoramento do Evolution API — 🔴 bloqueado por infra

**Estado real, verificado:** o Evolution API **não existe em produção ainda,
e nenhuma linha de Python fala com ele.** Grep por `evolution` no repo inteiro
retorna 3 arquivos, todos não-executáveis:
- `docker-compose.prod.yml:79-115` — serviço `atendai/evolution-api:v2.2.3` com
  Postgres e Redis próprios. O comentário no próprio arquivo diz que **o
  Railway não usa este compose** ("o Railway builda direto do Dockerfile da
  raiz") — ou seja, é um plano de migração para VPS, não o deploy atual.
- `docker/DEPLOY.md` — documentação do mesmo.
- `.env.example:54-55` — `EVOLUTION_API_URL` / `EVOLUTION_API_KEY`
  declaradas, **nenhuma lida por `settings.py`** (nenhum `env('EVOLUTION_…')`).

Contexto cruzado com o repo irmão: `EDGW-038` (`syncro_health`,
`.claude/tasks/GATEWAY-TASKS.md:331`) registra a decisão de 3 providers de
WhatsApp — Evolution API como default self-hosted, Meta Cloud API e Twilio
como alternativas onde a clínica traz as próprias credenciais. Essa task está
explicitamente marcada como **"registra a DECISÃO, não autoriza início de
código ainda"**. (Nota: o documento `EDGW-038-meta-cloud-api-design.md`
mencionado no pedido **não existe** em nenhum dos dois repos — só a entrada
no `GATEWAY-TASKS.md`.)

**Consequência direta:** monitorar o Evolution API hoje é monitorar um serviço
que não está de pé. **Esta parte do dashboard deve sair do escopo** até o
EDGW-038 virar código.

Para quando for a hora — o que o Evolution v2 expõe nativamente (a confirmar
contra a versão que for pinada; o próprio compose alerta que a superfície de
config muda entre versões):
- `GET /` — status/versão do servidor.
- `GET /instance/fetchInstances` (header `apikey`) — lista instâncias e o
  estado de conexão de cada uma (`open`/`close`/`connecting`). **É isso que
  responde "instâncias ativas".**
- Memória/consumo do container: **o Evolution não expõe.** Vem do
  orquestrador (Docker stats / painel da VPS), não da API dele.
Padrão de consumo: task Celery Beat a cada 60s → snapshot em tabela
(`integrations.ServiceHealthSnapshot`) → o dashboard lê a última linha.
**Nunca** chamar a API do Evolution no render da tela.

### 4.4 Custo de APIs — 🔴 nada existe, e o número real hoje é R$ 0,00

Inventário de APIs de terceiros efetivamente chamadas pelo backoffice:

| API | Onde | Modelo de custo | Dá pra rastrear? |
|---|---|---|---|
| **Orizon / TISS SOAP** | `tiss/soap_client.py` | **Não é cobrada por chamada.** É integração contratual operadora↔prestador. | Volume sim (§4.1). Custo: **não existe custo por chamada pra rastrear**. |
| **Asaas** (cobrança) | `billing/asaas.py` + webhook | Taxa **por transação recebida**, não por chamada de API. | Sim, mas é custo de meio de pagamento (linha de custo financeiro), não "custo de API". Vem do extrato do Asaas. |
| **Evolution API** | não implementado | **Self-hosted — custo é a VPS/container, não por mensagem.** | Não aplicável. |
| **Meta Cloud API** | não implementado | **Aí sim tem custo real por conversa** (Meta cobra por conversation window). | Só quando existir. E, segundo EDGW-038, **as credenciais são da clínica** — a fatura é dela, não nossa. |
| **Twilio** | não implementado | idem Meta. | idem. |
| Notion | legado, desativado | — | — |

**Veredito honesto: hoje não há custo de API por chamada para exibir.** Um
card de "custo de APIs" no MVP seria ou vazio ou uma estimativa inventada
(volume × um número chutado), o que é pior que não ter — vira um número que
alguém vai usar pra tomar decisão.

⚠️ **Preciso saber o que você quer medir de fato**, porque "custo das APIs"
pode ser três coisas diferentes:
- (a) **Custo de infra** (Railway/VPS, containers, Postgres) → vem da billing
  API do provider, não de código nosso. Card com link, no máximo.
- (b) **Custo por transação** (taxa Asaas) → é margem/financeiro, pertence a
  Administrar → Faturas, não a um dashboard de saúde.
- (c) **Custo de WhatsApp** → só existe se/quando Meta ou Twilio forem usados,
  e nesse desenho a conta é da clínica.

Se a resposta for "quero (a)", isso é um card estático com link, ~30min.
Se for "quero um sistema de tracking de custo por chamada", isso é uma
tabela + rateio + tabela de preço por API, e **eu recomendo não construir
antes de existir pelo menos uma API que cobre por chamada**.

### 4.5 Composição final proposta do dashboard (MVP realista)

```
┌──────────────────────────────────────────────────────────┐
│ CLIENTES        45 ativos · 3 inadimplentes · 2 inativos │  ← §5
├──────────────────────────────────────────────────────────┤
│ GATEWAYS        42/45 online   3 sem contato > 24h       │  ← já existe hoje
│                 (SystemHeartbeat + LastSeenFilter)        │
├──────────────────────────────────────────────────────────┤
│ OPERADORAS TISS                                           │  ← §4.1, tabela nova
│   Orizon (ANS 326305)   🟢  98% ok · p95 1.2s · 340 req  │
│   [outras aparecem sozinhas ao surgir tráfego]            │
├──────────────────────────────────────────────────────────┤
│ BANCO           18 conexões ativas · 340 MB · 99.2% hit  │  ← §4.2, cache 60s
│                 [ver detalhes no painel do provider →]    │
├──────────────────────────────────────────────────────────┤
│ ERROS (24h)     12 logs ERROR/CRITICAL   [ver →]         │  ← SystemLog, já existe
└──────────────────────────────────────────────────────────┘
Fora do MVP: Evolution API (§4.3, serviço não existe), custo de API (§4.4).
```

Regra de implementação inegociável: o `DASHBOARD_CALLBACK` **não pode** fazer
chamada de rede nem query pesada no request. Tudo agregado deve vir de
`cache.get_or_set(..., 60)` sobre o Redis que já está configurado, e as
agregações de `OperatorCallLog` precisam de índice em
`(registro_ans, created_at)`. Um dashboard de saúde que derruba o admin é
um resultado pior que não ter dashboard.

---

## 5. Administrar → Clientes + mini-dashboard

**O que já existe:** `clinics.Clinic` com `ClinicAdmin`
(`clinics/admin.py:7`), com `TenantScopedAdminMixin` (isolamento por
`ClinicAccess`) e `SENSITIVE_FIELDS` ocultos de quem não tem
`clinics.view_sensitive_clinic`. A listagem pedida **já está pronta e já está
no menu**. Só falta o resumo no topo.

**O que falta:** o mini-dashboard. E aqui está o problema real:

### 5.1 ⚠️ "Inadimplente" não é um estado que existe no sistema

`ClinicStatus` (`clinics/models.py:14`) tem exatamente três valores:
`active` / `suspended` / `cancelled`. Não há `overdue`/`inadimplente`.

E o webhook do Asaas (`billing/webhook_views.py:83-87`) faz isto:

```
elif event in ['PAYMENT_OVERDUE', 'PAYMENT_DELETED']:
    if clinic.status != ClinicStatus.SUSPENDED:
        clinic.status = ClinicStatus.SUSPENDED
```

Ou seja: **pagamento atrasado e suspensão administrativa colapsam no mesmo
estado.** Olhando o banco, é impossível distinguir "cliente que não pagou" de
"cliente que suspendemos por outro motivo". Exatamente a distinção que o
mini-dashboard pede.

Pior: existe `billing.Invoice` com `InvoiceStatus.OVERDUE` — o conceito está
modelado — mas **grep mostra que nada no fluxo Asaas cria ou atualiza
`Invoice`**. O webhook só mexe em `Clinic.status`. O model `Invoice` só é
tocado pelo `InvoiceViewSet` (CRUD REST manual) e pelos testes. Na prática
`Invoice` é uma tabela que ninguém alimenta automaticamente, e que **nem
aparece no admin** (§1.2).

Três caminhos, e **preciso da sua decisão**:

| Opção | O que é | Custo | Ressalva |
|---|---|---|---|
| **A** — derivar de `Invoice` | inadimplente = tem `Invoice` com `status=overdue` (ou `pending` com `due_date < hoje`) | ~2h + registrar `InvoiceAdmin` | **Só funciona se o webhook passar a criar/atualizar Invoice.** Hoje não cria → o card mostraria 0 sempre. Exige mexer no webhook (com idempotência — o Asaas reentrega evento). |
| **B** — novo `ClinicStatus.OVERDUE` | webhook passa a setar `overdue` em vez de `suspended` no `PAYMENT_OVERDUE` | ~1h (migration + webhook + ajuste do filtro) | Simples e resolve o card. Mas **muda semântica de negócio**: hoje inadimplente perde acesso na hora (`suspended`); com `overdue` separado, é preciso decidir se `overdue` ainda bloqueia o gateway. Checar `clinics/views.py::get_license_info`, que é quem responde ao gateway. |
| **C** — só espelhar o Asaas | consultar a API do Asaas na hora | — | ❌ **descartar** — chamada de rede no render do dashboard, ver §4.5. |

**Minha recomendação: B agora, A depois.** B entrega o card pedido em ~1h com
dado verdadeiro. A é a modelagem correta a longo prazo (histórico de faturas,
valor em aberto), mas exige primeiro consertar o webhook para materializar
`Invoice` — o que é uma task própria, com requisito de idempotência.

⚠️ **Decisão adicional necessária na opção B:** um cliente `overdue` continua
com licença válida? Isso é regra de negócio (quantos dias de tolerância antes
de suspender de fato), não decisão técnica. Não vou inventar.

### 5.2 O card, uma vez decidido

```
Ativos: Clinic.objects.filter(status=ACTIVE).count()
Inadimplentes: conforme decisão A ou B
Inativos: filter(status__in=[SUSPENDED, CANCELLED]).count()
```
Cada número é um link para o changelist já filtrado
(`/admin/clinics/clinic/?status__exact=active`) — sem tela nova.
Renderizado via `UNFOLD["DASHBOARD_CALLBACK"]` (mesmo do §4) ou, se você
quiser o resumo **dentro** da listagem, via `changelist_view` sobrescrito com
`extra_context` + template estendendo `unfold/change_list.html`.
⚠️ Se o card for cravado no changelist, ele **precisa respeitar o
`TenantScopedAdminMixin`** — um analista com `ClinicAccess` para 2 clínicas
não pode ver a contagem global. Usar `self.get_queryset(request)` como base
da agregação, nunca `Clinic.objects.all()`. Isso é vazamento de dado de
negócio se feito errado.

---

## 6. Plano de execução sugerido (fatiado, MVP-first)

| # | Task | Depende de | Esforço | Veredito |
|---|---|---|---|---|
| 1 | Reorganizar `UNFOLD["SIDEBAR"]` (§3), remover `TABS` morto, ajustar `ENVIRONMENT` | — | 1h | ✅ pode começar |
| 2 | Registrar `InvoiceAdmin` + `PlanAdmin` (§1.2) | — | 1h | ✅ pode começar |
| 3 | `DASHBOARD_CALLBACK` com os 3 cards que usam dado já existente (Clientes ativos/inativos, Gateways, Erros 24h) | 1 | 2h | ✅ pode começar |
| 4 | Card de inadimplentes | ⚠️ decisão §5.1 | 1-3h | bloqueado por você |
| 5 | `tiss.OperatorCallLog` + instrumentação do `soap_client` + card por operadora + purga 90d | ⚠️ confirmar §4.1(a) | 3h | quase pronto pra ir |
| 6 | Card de banco (conexões/tamanho/cache hit, cache 60s) | ⚠️ decisão §4.2 | 2h | bloqueado por você |
| 7 | CBO / CID-10 como tabelas de referência | ⚠️ decisão §3.1 | 4-6h cada | feature nova, não reorg |
| 8 | Health do Evolution API | 🔴 EDGW-038 | — | fora de escopo |
| 9 | Tracking de custo de API | ⚠️ decisão §4.4 | — | não construir ainda |

Tasks 1, 2 e 3 são independentes de qualquer decisão sua e entregam a maior
parte da percepção de "arrumado". Recomendo fazer as três primeiro e revisitar
o resto com o dashboard já de pé.

---

## 7. Nada aqui foi implementado

Este documento é pesquisa e desenho. Nenhum código foi escrito, nenhum
`settings.py` alterado, nenhuma migration criada.

---

## 📌 Chamado para o Agente QA — escopo de teste quando isto virar código

- **Menu/permissões:** para cada um dos 5 grupos, um teste por perfil
  (superuser, `role=admin`, Analista Operacional, usuário sem grupo)
  verificando que itens sem permissão **não renderizam** — e que a URL direta
  do changelist correspondente retorna 403, não 200. Menu escondido não é
  controle de acesso.
- **Cards do dashboard:** teste de contagem com clínicas em cada `status`,
  incluindo o caso zero (nenhuma clínica) e o caso de analista com
  `ClinicAccess` para 2 de 45 clínicas — o card deve mostrar 2, não 45.
- **`OperatorCallLog`:** cenários de borda da classificação — 0 chamadas na
  janela (deve dar ⚪ "sem tráfego", nunca 🟢), exatamente 1 chamada com erro
  (não deve virar 🔴 por causa do mínimo de 3), erro em 50% cravado (fronteira
  🟡/🔴), latência com um único outlier (p95 vs média).
- **Regressão de performance:** contar queries do `DASHBOARD_CALLBACK` com
  `assertNumQueries` e travar num teto; e um teste que o cache de 60s
  realmente evita a segunda query. Um dashboard N+1 sobre 45 clínicas é
  falha de aceite.
- **Webhook Asaas (se opção B):** reentrega do mesmo `PAYMENT_OVERDUE` não
  pode gerar efeito duplicado; e a transição `overdue → active` no
  `PAYMENT_RECEIVED` precisa de teste explícito (hoje o código só checa
  `!= ACTIVE`).
- **Migração de menu:** teste que todos os `reverse_lazy` do sidebar resolvem
  — o bug do `TABS` (§1.2) existiu por meses justamente por falta disso.

## 🛡️ Chamado para o Agente de Segurança — riscos a auditar

- **IDOR / vazamento cross-tenant no dashboard:** o `DASHBOARD_CALLBACK`
  roda fora do `ModelAdmin`, portanto **fora do `TenantScopedAdminMixin`**.
  Qualquer agregação escrita com `Model.objects.all()` vaza contagem e
  saúde de clínicas para um analista escopado. É o risco nº1 desta task.
- **Exposição de credencial de terceiro:** trazer `TISSOperatorConfig` para
  o menu de Configurações aumenta a superfície de acesso a
  `login_encrypted`/`senha_encrypted`. Confirmar que os campos seguem fora de
  `list_display`, `search_fields`, `readonly_fields` e do form — e que a
  permissão do item de menu é mais restrita que `view_tissoperatorconfig`
  genérico.
- **PHI em log de chamada (LGPD):** `OperatorCallLog` **não pode** guardar
  request/response XML nem `erro_mensagem` cru — a mensagem de fault da
  operadora frequentemente ecoa carteirinha/nome do beneficiário. Só código
  de erro normalizado. Auditar também se `TISSElegibilidadeConsulta.
  erro_mensagem` (já existente) tem esse problema hoje.
- **`pg_stat_activity` no admin:** a coluna `query` expõe SQL em execução
  com valores literais — potencialmente CPF/dados de paciente. Se o card de
  banco for implementado, selecionar apenas `state` e `count(*)`, **nunca**
  `query`. E confirmar que o usuário do Django não tem `pg_read_all_stats`
  além do necessário.
- **Segredo do Evolution:** `EVOLUTION_API_KEY` é chave de admin do serviço
  inteiro (envia mensagem por qualquer instância). Se o health-check for
  implementado, ela não pode aparecer em log, em erro de request, nem no
  admin — e o cliente HTTP precisa de timeout curto para não virar SSRF/DoS
  interno pelo render do dashboard.
- **Superfície nova sem rate limit:** o `/admin/` já tem `LoginRateThrottle`
  só no login da API. Um dashboard que dispara agregação pesada a cada F5 é
  um DoS autenticado barato — o cache de 60s é controle de segurança, não só
  de performance.
