# ARCH-CELERY-SPLIT-001 — Separar Celery Worker do Django Web (Railway)

**type:** infra
**priority:** P1
**detected_by:** Principal Architect (tech-lead, 2026-07-09)
**status:** planned

---

## Achado crítico — verificar antes de tudo

Celery já está configurado (`syncro_backoffice/celery.py`, broker/backend
Redis em `settings.py:200-208`, `CELERY_TASK_ALWAYS_EAGER` default
`DEBUG` — em produção com `DEBUG=False` isso resolve para `False`, ou
seja, as tasks **não** rodam de forma síncrona/eager). Existem 2 tasks
hoje: `clinics.tasks.sync_modules_to_edge` e
`support.tasks.sync_ticket_to_notion`.

**Não encontrei nenhum Procfile, `railway.json`/`railway.toml`, ou script
de start no repositório** — a configuração de start command do Railway
vive só no dashboard (mesmo padrão já visto com o bug do `gunicorn`
ausente do `requirements.txt`, corrigido antes nesta sessão). **Isso
significa que preciso que você confirme**: hoje existe um segundo
serviço/processo no Railway rodando `celery -A syncro_backoffice worker`,
ou as tasks estão só se acumulando na fila do Redis sem ninguém
consumindo? Se for o segundo caso, isso já é um bug de produção
silencioso, independente da decisão de arquitetura abaixo.

---

## Por que separar (concordo com o diagnóstico)

1. **Modelo do Railway é 1 serviço = 1 processo escalável independente.**
   Hoje, se web e worker rodam no mesmo dyno/processo, um pico de carga
   de qualquer um dos dois lados rouba CPU/memória do outro — um envio
   em massa de WhatsApp trava requisições HTTP do backoffice, e
   vice-versa.
2. **Crescimento assimétrico esperado.** Você está certo que o volume de
   trabalho assíncrono (WhatsApp + integração com operadoras) tende a
   crescer mais rápido que o tráfego HTTP do backoffice (que é
   majoritariamente uso interno da equipe SyncroHealth + poucos
   `ClinicUser`s por enquanto). Escalar os dois juntos desperdiça
   recursos — você paga por CPU do web parado enquanto o worker está
   sobrecarregado, ou vice-versa.
3. **Deploy independente.** Um bug no código de envio de WhatsApp não
   deveria exigir redeploy (e possível downtime) do backoffice web
   inteiro, e vice-versa.
4. **Sem necessidade de Celery Beat por enquanto — mas revisitar quando
   TISS SOAP for implementado.** Não há nenhuma task periódica hoje
   (`CELERY_BEAT_SCHEDULE` não existe). Porém: o plano já registrado
   para envio TISS via SOAP (Orizon, TASK-BO-08, hoje bloqueado por
   falta de docs ANS/credenciais sandbox) é **assíncrono em duas
   fases** — envia o lote, recebe um número de protocolo, e precisa
   fazer *polling* (`solicitacaoStatusProtocolo`) pra saber o resultado
   final (aceite/glosa). Quando essa integração for desbloqueada e
   implementada, o polling periódico **vai** justificar Celery Beat (ou
   uma task com retry/`countdown` agendado) — não é over-engineering
   nesse caso específico, é exigência do próprio protocolo. Não criar
   agora, mas não é "nunca".

---

## Redis vs RabbitMQ como broker

**Recomendação: manter Redis.** A resiliência que importa aqui —
"não posso enviar o mesmo lote TISS duas vezes pra operadora" — não é
resolvida pela escolha do broker. Celery com qualquer broker (Redis ou
RabbitMQ) usa entrega *at-least-once*: uma task pode ser reentregue se
o worker cair no meio da execução antes de confirmar (`ack`). O que
evita duplicidade é sempre lógica de aplicação, não o transporte de
mensagens.

### Design de idempotência recomendado (independe do broker)

Para a futura task de envio TISS especificamente:

1. **Transição de estado atômica no Postgres antes de chamar a
   operadora**, tipo `UPDATE billing_batch SET status='sending' WHERE
   id=%s AND status='pending'` — se afetar 0 linhas, outro
   worker/redelivery já reivindicou aquele lote, aborta.
2. `acks_late=True` + `task_reject_on_worker_lost=True` na task de
   envio — só confirma a mensagem depois que a chamada SOAP realmente
   terminou, nunca antes de começar.
3. `visibility_timeout` alto especificamente para essa fila (via
   `broker_transport_options` do Celery no Redis) — Orizon é
   assíncrono e pode demorar; timeout curto causaria redelivery
   prematura e envio duplicado.
4. **Envio e polling de status em tasks separadas** — reforça o ponto
   acima sobre Celery Beat: quando implementado, o polling deve ser uma
   task própria, agendada, não parte da task de envio.

### Quando RabbitMQ passaria a valer a pena

Se o volume ficar realmente alto, ou surgir necessidade real de
dead-letter queue nativa, ack/nack mais robusto, ou filas com
prioridade — nesse ponto migrar pra RabbitMQ é justificável. Não é a
decisão certa agora, com a integração TISS SOAP ainda bloqueada por
falta de documentação/credenciais — revisitar quando ela for
desbloqueada e o volume real for conhecido.

## Desenho recomendado

**2 serviços Railway, mesmo repositório, mesmo código-fonte — só o start
command muda:**

| Serviço | Start command | Escala |
|---|---|---|
| `backoffice-web` | `gunicorn syncro_backoffice.wsgi:application` | Baseado em tráfego HTTP (réplicas/CPU conforme uso da equipe + `ClinicUser`s) |
| `backoffice-worker` | `celery -A syncro_backoffice worker -l info --concurrency=2` | Baseado em volume de mensagens WhatsApp + chamadas às operadoras |

Ambos compartilham:
- O mesmo banco Postgres (Neon, já em uso)
- O mesmo Redis (broker Celery — Railway tem plugin Redis gerenciado,
  usar o mesmo para os dois serviços aponta pra ele via `CELERY_BROKER_URL`)
- As mesmas variáveis de ambiente de credenciais (WhatsApp/Meta ou
  Twilio ou EvolutionAPI, credenciais de operadoras) — **não duplicar
  segredos manualmente entre os 2 serviços**; usar "Shared Variables" do
  Railway se disponível no seu plano, ou replicar com cuidado.

### Por que não usar o mesmo processo com `--pool=solo` ou threads internas

Já vi esse padrão em outros projetos como atalho ("rodar o worker numa
thread dentro do mesmo processo gunicorn") — não recomendo aqui: perde
justamente o isolamento de recursos que é o motivo #1 de fazer a
separação. Só faz sentido como atalho temporário se o objetivo fosse só
"não pagar por 2 serviços agora", o que não parece ser sua preocupação —
você já identificou que quer escalar os dois de forma independente.

## Como fica o `sync_clinic_modules` (task já existente)

`sync_modules_to_edge` sincroniza módulos de uma clínica com o Gateway
Edge (chamada de rede para fora do Railway, para a máquina da clínica) —
já é candidata natural a rodar no worker separado, sem mudança de
código, só de onde o processo `celery worker` roda.

## Roteamento de filas (queues) — recomendação para o WhatsApp/operadoras

Quando as tasks de WhatsApp e integração com operadoras forem
implementadas (ainda não existem — `integrations/` app só tem
models/views/admin, sem `tasks.py`), recomendo desde já usar **filas
nomeadas** (`@shared_task(queue='whatsapp')`, `@shared_task(queue='insurance_sync')`)
em vez de uma fila default única. Isso permite, no futuro, rodar workers
dedicados por fila (`celery worker -Q whatsapp --concurrency=4`) sem
precisar reestruturar nada — só separar o comando de start se um dia o
volume de uma das duas crescer desproporcionalmente à outra. Não é
necessário implementar isso agora (não há tasks ainda), só adotar a
convenção de nomear a fila desde a primeira task de WhatsApp/operadora
que for escrita, para não pagar retrabalho depois.

## Passos de implementação

1. **Confirmar no dashboard do Railway** se já existe um segundo serviço
   rodando o worker (achado crítico acima) — se não existir, isso é
   bug de produção a corrigir primeiro, não só arquitetura.
2. Criar o serviço `backoffice-worker` no Railway apontando para o
   mesmo repo/branch, start command `celery -A syncro_backoffice worker -l info --concurrency=2`.
3. Confirmar que `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` apontam
   para a mesma instância Redis nos dois serviços.
4. Adicionar ao repo (documentação, não é estritamente necessário pro
   Railway funcionar, mas ajuda observabilidade/rastreabilidade): um
   `Procfile` ou `railway.json` explícito com os 2 processos, para não
   depender só de configuração manual no dashboard (mesmo problema que
   já causou o bug do `gunicorn` ausente).
5. Monitorar memória/CPU do worker separadamente por 1-2 semanas antes
   de decidir `--concurrency` final.
6. Quando WhatsApp/integração com operadoras forem implementados,
   nomear as filas desde o início (ver seção acima).

## Acceptance criteria

- [ ] Confirmado com o usuário se o worker já roda separado hoje no
      Railway (bloqueante para as próximas etapas)
- [ ] Serviço `backoffice-worker` criado no Railway com start command
      dedicado
- [ ] Variáveis de ambiente (Redis, credenciais) replicadas/compartilhadas
      corretamente entre os 2 serviços
- [ ] `Procfile`/`railway.json` adicionado ao repo documentando os 2
      processos
- [ ] Sem Celery Beat criado agora (justificado: sem tasks periódicas
      hoje)
- [ ] Convenção de fila nomeada (`queue=`) documentada para as futuras
      tasks de WhatsApp/operadoras

## Estimativa

2-3h de configuração + tempo de observação (1-2 semanas) antes de
fechar dimensionamento de concorrência do worker.
