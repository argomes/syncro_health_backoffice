# Deploy do backoffice — Railway hoje, VPS amanhã

Arquitetura pensada pra não prender você ao Railway: o `Dockerfile` na raiz
é a unidade de deploy em qualquer lugar (Railway builda direto dele; uma VPS
roda a mesma imagem via `docker-compose.prod.yml`). Nenhuma lógica de
negócio depende de recurso específico do Railway.

## Railway (hoje)

Railway detecta o `Dockerfile` na raiz automaticamente e builda por ele — não
precisa de `railway.toml` a menos que você queira fixar o healthcheck path
explicitamente (`/health/`, já criado). Suba como **serviços separados** no
mesmo projeto Railway:

1. **backoffice** (web) — usa o `Dockerfile` da raiz, comando padrão
   (`CMD` já definido no Dockerfile: gunicorn). Configure a porta 8000 e o
   healthcheck em `/health/`.
2. **worker** — mesmo repo/Dockerfile, mas sobrescreva o comando de start no
   Railway pra `celery -A syncro_backoffice worker --loglevel=info`. Sem
   isso, tickets do Notion e módulos de clínica que dependem de Celery
   silenciosamente nunca processam (ou processam síncrono se
   `CELERY_TASK_ALWAYS_EAGER` estiver mal configurado — não confie nisso em
   produção).
3. **Postgres** — plugin gerenciado do Railway (ou continue no Neon, como
   está hoje em `.env.production`) — não precisa ser um container.
4. **Redis** — plugin gerenciado do Railway.
5. **evolution-api** — se for rodar Evolution API no próprio Railway (em vez
   de numa VPS separada), sobe como serviço a partir da imagem pública
   `atendai/evolution-api` (Railway aceita "Deploy from Docker Image" direto,
   sem precisar de Dockerfile próprio). Precisa de um Postgres e Redis
   dedicados também — pode reaproveitar plugins gerenciados do Railway, um
   para cada, para não acoplar o schema dele ao banco de negócio.

Variáveis de ambiente: copie `.env.example`, preencha os valores reais. Os
mais fáceis de esquecer (o app falha alto no boot se faltar, de propósito):
`SECRET_KEY`, `DATABASE_URL`, `CACHE_URL` (Redis — obrigatório com
`DEBUG=False`, ver comentário em `settings.py`), `ALLOWED_HOSTS` (inclua o
domínio `*.railway.app` do serviço).

## VPS (futuro)

Quando migrar: `docker compose -f docker-compose.prod.yml --env-file
.env.production up -d` sobe tudo — backoffice, worker, Postgres, Redis,
Evolution API e a infra dele. Único pré-requisito: Docker + Docker Compose
instalados na VPS, mais um proxy reverso na frente (Caddy/nginx/Traefik) pra
terminar TLS e apontar pro `backoffice:8000` — `SECURE_PROXY_SSL_HEADER` já
está configurado em `settings.py` pra confiar em `X-Forwarded-Proto` do
proxy, então HTTPS/cookies seguros funcionam sem mudança de código nessa
migração.

Não incluí o proxy reverso no compose de propósito — a escolha entre
Caddy (certificado automático, mais simples) e nginx (mais controle) é sua
a fazer quando a VPS existir de verdade; adicionar um serviço de proxy que
não existe ainda seria overengineering.

## Evolution API — cuidados

- A imagem pinada no `docker-compose.prod.yml` (`atendai/evolution-api:v2.2.3`)
  deve ser conferida contra a versão atual antes do primeiro deploy — o
  projeto muda variáveis de configuração entre versões, então **não** atualize
  a tag sem revisar o changelog deles.
- `AUTHENTICATION_API_KEY` protege a API do próprio Evolution — trate como
  segredo, nunca reaproveite o mesmo valor de outro serviço.
- O backoffice ainda não tem código de integração com o Evolution (TASK-037,
  status `planned`) — este compose só prepara a infra. Alertas via WhatsApp
  (gateway offline, erro crítico) exigem implementar o client HTTP no
  backoffice chamando a API do Evolution, que é trabalho separado.

## Antes do primeiro deploy real

- [ ] `.env.production` preenchido com segredos reais, nunca commitado (já no `.gitignore`)
- [ ] `CACHE_URL` configurado — sem isso o processo nem sobe com `DEBUG=False` (guarda intencional, ver `settings.py`)
- [ ] `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS` com o domínio real
- [ ] Confirmar que o worker Celery está rodando como processo separado (não é opcional — Notion sync e módulos de clínica dependem dele)
