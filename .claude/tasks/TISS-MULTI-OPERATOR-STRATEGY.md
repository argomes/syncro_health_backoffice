# Estratégia multi-operadora TISS: Orizon como transporte, e a pergunta do repositório próprio

**Data:** 2026-07-28
**Autor:** Principal Architect (pesquisa + análise, a pedido do Tech Lead)
**Status:** documento de decisão — **nenhum código escrito**
**Repositório:** `syncro-backoffice`
**Substitui premissa de:** `.claude/tasks/TISS-OPERATOR-PROVIDER-ARCHITECTURE.md` (§3, §4) — aquele documento tratava "Orizon" como uma operadora com provider próprio. A premissa está errada e este documento corrige.
**Implementação em curso afetada:** branch `feat/tiss-provider-architecture` (commit `cb6ff42`)

---

## Status de implementação — §2 (defeito de credencial duplicada)

**CORRIGIDO.** PR [#50](https://github.com/argomes/syncro_health_backoffice/pull/50) (branch `fix/tiss-operator-connection-split`, aberto contra `develop`, ainda não mesclado no momento deste registro).

Implementado exatamente como desenhado em §2/§3.3 (a parte de separação
Connection/Config; `tiss/operadoras/` — item 4 da ordem prática em §7 —
continua não implementado, como recomendado, até a clínica-piloto revelar a
operadora real):

- `TISSOperatorConnection` (novo model): clínica + transporte + endpoint +
  credencial, `unique_together = [('clinic', 'endpoint_url', 'gateway_provider')]`.
- `TISSOperatorConfig` reduzido a clínica + `registro_ans` + particularidades
  da operadora real + FK `connection`.
- Migração de dados (`tiss/migrations/0011_backfill_tissoperatorconnection.py`,
  entre as migrations de schema `0010`/`0012`) agrupa as `TISSOperatorConfig`
  existentes por `(clinic, endpoint_url, gateway_provider)` e consolida cada
  grupo numa única `TISSOperatorConnection`, sem perder credencial.
- `providers/orizon.py`, `providers/generico_ans.py` e
  `orizon_autorize_xml_builder.py` passam a ler
  `operator_config.connection.{endpoint_url,login_plain,senha_plain}`.
- Teste de unicidade de credencial (duas configs do mesmo transporte
  compartilham a mesma connection) e teste de migração simulando o cenário
  real (3 operadoras duplicadas do documento consolidando numa connection
  só) — ver `tiss/tests_models.py::TISSOperatorConnectionTests` e
  `tiss/tests_migration_operator_connection.py`.
- Suíte `tiss` completa: 155/155 verdes, sem regressão em `tests_providers.py`.

**Sugestão não executada:** não existe issue formal para este defeito (só
estava documentado aqui). Pode valer a pena abrir uma para rastreabilidade
de versão/changelog — decisão do Tech Lead.

---

## 0. Veredito em 7 linhas

1. **A Orizon NÃO abstrai a particularidade da operadora.** Ela abstrai **transporte** (um endpoint, um envelope, uma credencial, uma versão do padrão). O conteúdo do XML continua tendo que saber se o beneficiário é Bradesco, Cabesp ou Cassi. Evidência de código e de manual em §1.
2. **O roteamento já é por operadora real, não pela Orizon.** O Edge Gateway manda `registro_ans` da operadora do beneficiário (`005711` = Bradesco) e o backoffice resolve `TISSOperatorConfig` por `(clinic, registro_ans)`. A Orizon nunca é a chave — ela é um atributo daquela linha.
3. **`orizon.py` como provider único está CERTO como camada de transporte e ERRADO se virar o dono das regras de negócio das 5+ operadoras atrás dele.** A correção é aditiva, não uma reescrita: §3.
4. **O defeito de modelagem real e imediato não é o provider — é a credencial.** Com 5 operadoras atrás da Orizon, a clínica cria 5 linhas de `TISSOperatorConfig` com `endpoint_url` e login/senha **idênticos**. Uma rotação de senha vira 5 escritas; um esquecimento vira falha silenciosa em produção. É um risco LGPD e operacional, não estético. §2.
5. **Separar em repositório/serviço próprio: NÃO. Nem agora, nem no horizonte de 12 meses previsível.** Justificativa e os três gatilhos concretos que mudariam essa resposta em §5.
6. **O módulo `tiss/` já é quase extraível** — nenhum outro app do Django importa `tiss`, o acoplamento é unidirecional e raso (3 pontos). Isso é o que torna "não separar" seguro: a opção continua barata. §6 lista as 5 invariantes para mantê-la barata.
7. **`feat/tiss-provider-architecture` NÃO deve ser pausada.** O contrato que ela criou é exatamente o que torna a correção da premissa barata. Ajuste de rumo cirúrgico em §7.

---

## 1. Como a Orizon realmente funciona (confirmado no código)

### 1.1 O que a Orizon abstrai

Leitura de `tiss/orizon_autorize_xml_builder.py`, `tiss/orizon_autorize_client.py`, `tiss/views.py` e `tiss/models.py`:

| Dimensão | Quem resolve | Evidência |
|---|---|---|
| Endpoint SOAP | **Orizon** — um só (`wsp.orizonbrasil.com.br:6213/tiss/v40100/...`) para todas as operadoras atrás dela | `orizon_autorize_client.py`, docstring de endpoints |
| Credencial | **Orizon** — login/senha do prestador cadastrado no Portal Orizon, MD5 no `<loginSenhaPrestador>` | `_cabecalho_xml()` |
| Envelope / operação | **Orizon** — `solicitacaoProcedimentoWS`, sem `<mensagemTISS>`/`<epilogo>` do padrão ANS | `build_solicitacao_procedimento_xml()` |
| Versão do padrão | **Orizon** — 4.01.00, defasada em relação à ANS | `TISS_PADRAO_VERSAO_ORIZON` |
| Roteamento para a operadora certa | **Orizon**, a partir de `<destino><registroANS>` que **nós** preenchemos | `_cabecalho_xml()`: `<sch:destino><sch:registroANS>{registro_ans}</sch:registroANS></sch:destino>` |

Até aqui a Orizon é um agregador limpo: **um transporte, N destinos**.

### 1.2 O que a Orizon NÃO abstrai — o achado que muda o desenho

O manual oficial do Autorize (Cap. 8, já lido e resumido em `TISS-OPERATOR-PROVIDER-ARCHITECTURE.md` §5 item 5) documenta particularidades que são **campos do XML que nós montamos**, não comportamento que a Orizon normaliza:

- **Bradesco tem dois registros ANS distintos** (`005711` e `421715`). Escolher o errado gera negativa automática. Nós escolhemos.
- **Bradesco exige Solicitação de Senha prévia** (`tipoEtapaAutorizacao=1`) com auto-cancelamento em 12h — um fluxo de duas etapas que só existe para essa operadora.
- **Cabesp e Cassi exigem sequencial de endereço com dígitos fixos após a matrícula** — regra de formatação do `numeroCarteira`. Nós formatamos.
- **`codigoPrestadorNaOperadora` tem semântica diferente por operadora:** Bradesco usa "Código Centralizador"; Cassi/Cabesp usam o sequencial. Mesmo campo XML, significado diferente.
- **Economus, CarePlus e Seguros Unimed têm regras próprias de negativa** — o mesmo `codigoGlosa` (ex.: 3144) não significa a mesma ação corretiva em todas.

O código atual **reconhece a lacuna e a deixa aberta explicitamente**. Em `orizon_autorize_xml_builder.py::_solicitacao_sp_sadt_xml()`:

> *"Particularidades por operadora (Bradesco, Cabesp, Cassi, Economus, Careplus, Seguros Unimed — ver Cap. 8 do manual) NÃO estão implementadas aqui ainda"*

E o builder hoje emite **constantes hardcoded** onde essas particularidades deveriam entrar — `codigoPrestadorNaOperadora=0`, `CNES=0000000`, `ausenciaCodValidacao=01`, `nomeContratadoSolicitante='Clinica'`. Isso é aceitável enquanto nada foi para homologação; deixa de ser no minuto em que a primeira clínica-piloto for Bradesco.

### 1.3 Conclusão de topologia

> **A Orizon é um `TransportMechanism`, não uma operadora.** A unidade de variação de negócio é a **operadora real**. A unidade de variação de protocolo é o **transporte** (Orizon, ou SOAP direto da operadora, ou um futuro agregador concorrente).
>
> São **dois eixos independentes**, e o desenho atual só tem um.

Isso não é teoria: a mesma operadora pode ser alcançável pelos dois eixos. Uma clínica alcança Bradesco pela Orizon; outra, credenciada direto, alcança Bradesco pelo webservice do Bradesco. As regras de negócio (2 registros ANS, senha prévia, código centralizador) são **as mesmas nos dois casos**. Se essas regras morarem dentro de `providers/orizon.py`, elas precisam ser reescritas quando surgir o caminho direto. Se morarem num módulo por operadora, são reusadas.

---

## 2. O defeito imediato: credencial duplicada N vezes

Independente de qualquer refatoração de provider, há um problema concreto **hoje** em `TISSOperatorConfig`:

```
unique_together = [('clinic', 'registro_ans')]
```

Uma clínica que fatura Bradesco, CarePlus, Cabesp, Cassi e Seguros Unimed pela Orizon precisa de **5 linhas**, cada uma com:
- `endpoint_url` — idêntico nas 5
- `login_encrypted` / `senha_encrypted` — **idênticos nas 5**
- `gateway_provider='orizon'` — idêntico nas 5
- `registro_ans` — a única coisa que de fato varia

Consequências, em ordem de gravidade:

1. **Rotação de credencial vira 5 escritas.** A Orizon expira senha; um operador de suporte atualiza 4 de 5 e uma operadora para de faturar silenciosamente até alguém reclamar. Isso é risco de **continuidade de faturamento da clínica** — a categoria de risco que priorizamos sobre escalabilidade teórica.
2. **Superfície de segredo multiplicada por 5.** Cada linha é mais um lugar de onde o mesmo segredo pode vazar (admin, export, backup, log de auditoria). O Fernet protege o at-rest; não protege a proliferação.
3. **Escala mal exatamente na direção que o Tech Lead quer ir.** "A maior quantidade de operadoras que conseguir" × N clínicas = duplicação linear de segredos.

**Correção (barata, e independente da discussão de repositório):** separar `TISSOperatorConnection` (clínica + transporte + endpoint + credencial, 1 linha por clínica-por-agregador) de `TISSOperatorConfig` (FK para a connection + `registro_ans` + particularidades da operadora). Migração de dados trivial: agrupar linhas existentes por `(clinic, endpoint_url, gateway_provider)`.

Isso é o **mesmo movimento** que corrige a modelagem de §1.3, de graça. Não são duas refatorações — é uma.

---

## 3. Impacto no desenho de provider em implementação

### 3.1 O que está certo e deve ficar

O contrato de `providers/base.py` (`verificar_cobertura`, `enviar_lote`, `health_check`, `capabilities`) é **bom e independente desta discussão**. Especificamente:

- **A decisão D1** (unificar elegibilidade+autorização em `verificar_cobertura`) fica ainda **mais** certa com a premissa corrigida: é ela que impede `if operadora == 'X'` de vazar para a UI e para o gateway Go — e vamos ter muito mais operadoras do que se supunha.
- **`resolve()` como switch único** com checagem de `ativo` no ponto de despacho: correto, e já corrigiu dois bugs reais (#45, #46).
- **`OperatorCallLog` + `health_check` genérico:** correto, e vira mais valioso com N operadoras — é a única forma de responder "a Bradesco está fora ou é a Orizon inteira?" sem SSH.
- **Teste anti-vazamento parametrizado sobre `_PROVIDERS`, bloqueante de merge:** correto. Mantém.

### 3.2 O que está errado

`_PROVIDERS` está chaveado por `TISSGatewayProvider` — **um eixo só**. Com a premissa corrigida, existem dois:

```
transporte:  orizon | generico_ans | (futuro: bradesco_direto, sulamerica_direto)
operadora:   bradesco | careplus | cabesp | cassi | seguros_unimed | ...
```

O risco concreto se isso não for corrigido: as particularidades do Cap. 8 vão entrar em `providers/orizon.py` como uma cadeia de `if registro_ans == '005711'`. Esse arquivo vira o God Object do módulo, e é exatamente o `if operadora ==` que o próprio docstring de `providers/__init__.py` se propõe a banir — reintroduzido um nível abaixo, onde o teste de contrato não olha.

### 3.3 A correção (aditiva, ~1 dia de trabalho)

Nenhuma assinatura do contrato muda. Adiciona-se um segundo registro, consultado **pelo transporte**, não pelo chamador:

- `tiss/providers/` (existente) continua sendo **transportes**. `orizon.py` monta envelope, autentica, fala SOAP, normaliza resposta. Deixa de conhecer qualquer operadora nominalmente.
- `tiss/operadoras/` (novo) — um módulo por operadora real, com o que é **dela**, não do transporte: registros ANS válidos, formatação de `numeroCarteira`, semântica de `codigoPrestadorNaOperadora`, exigência de senha prévia, mapa de códigos de negativa para ação. Sem I/O. Sem Django ORM.
- Default explícito `operadoras/padrao_ans.py` para toda operadora sem particularidade conhecida — que será a maioria. **Adicionar operadora nova continua sendo zero código** enquanto ela se comportar como o padrão; só ganha módulo quando revelar uma exigência própria.

Essa fronteira é a que permite reusar as regras do Bradesco quando/se surgir o caminho direto, e é a que faz "adicionar operadora" custar ~0 no caso comum — que é o requisito real do Tech Lead.

---

## 4. Quanto o módulo `tiss/` está acoplado ao resto do Django

Medido, não estimado:

**Ninguém importa `tiss`.** Grep em `accounts`, `billing`, `clinics`, `integrations`, `metrics`, `municipios`, `portal_gestor`, `support`, `syncro_backoffice`, `holidays`: **zero** ocorrências de `from tiss` / `import tiss`. O acoplamento é estritamente unidirecional.

**`tiss` importa de fora em 3 pontos** (excluindo testes):

| Arquivo | Import | Natureza |
|---|---|---|
| `models.py` | `clinics.models.Clinic` | FK de tenancy — a única real |
| `views.py` | `accounts.models.{SupportUser, ClinicAccess}`, `clinics.permissions.IsAuthenticatedByLicenseKey` | autenticação/autorização, camada de borda |
| `edge_client.py` | `clinics.service_tokens.generate_service_token`, `clinics.services._is_safe_url` | chamada de saída para o Edge Gateway |

Traduzindo: o núcleo TISS (builders, clients, providers, services) **não conhece paciente, agendamento, faturamento nem portal**. Já é hexagonal na prática. `TISSGuia.appointment_id` é deliberadamente uma referência opaca, não FK — o backoffice nunca teve acesso ao banco clínico.

**Nota:** `edge_client.py` importa `clinics.services._is_safe_url` — função privada de outro app. É o único import que viola encapsulamento e o único candidato a virar dor numa extração. Promover para uma utilidade pública compartilhada (ex.: `syncro_backoffice/net.py`) é trabalho de 20 minutos.

---

## 5. Veredito: separar em repositório/serviço próprio?

### **NÃO. E a razão principal não é a regra anti-microserviço — é que a separação não resolve nenhum problema que temos.**

Vamos contra os quatro pesos levantados:

**(a) Acoplamento — argumento a FAVOR de separar, e é o único forte.** §4 mostra que o custo técnico de extrair é baixo. Mas "é barato separar" não é razão para separar; é razão para **não ter pressa**. Uma opção barata que continua barata pode ser exercida depois, com mais informação. Exercê-la agora queima a informação que ainda não temos (quais operadoras de fato, qual volume, qual equipe).

**(b) Volume — argumento CONTRA, decisivo.** As chamadas TISS são: uma verificação de cobertura por atendimento agendado, e um envio de lote por competência (mensal). Uma clínica média faz dezenas de atendimentos/dia. Cem clínicas fazem milhares de chamadas/dia — **carga trivial para um Django**. E o gargalo real não é CPU nossa: é a latência do SOAP da operadora, resolvida com worker/timeout, não com um deploy separado. Não há perfil de escalabilidade independente a justificar. Separar por escalabilidade aqui seria overengineering pelo dicionário.

**(c) Manutenção — argumento NEUTRO, e frequentemente mal contado.** "Adicionar operadora sem tocar no monólito" já é verdade hoje: `tiss/` é um app Django isolado, e depois de §3.3 adicionar operadora comum é um arquivo novo ou nada. O ganho marginal de um repo separado é **zero** aqui. O custo é real e recorrente: mais um deploy, mais um pipeline de CI, mais um alvo de monitoração, mais um lugar onde a versão pode divergir, e — o mais caro — **autenticação serviço-a-serviço** para chamadas que hoje são chamadas de função. Trocar `resolve(config)` por um HTTP call autenticado adiciona uma classe inteira de modos de falha (timeout, retry, entrega parcial) a um caminho que hoje é síncrono e determinístico.

**(d) Independência de domínio — argumento a favor de MÓDULO isolado, não de SERVIÇO.** É verdade que o núcleo TISS não precisa de paciente/agendamento. Mas ele precisa de `Clinic` (tenancy) e de `ClinicAccess` (autorização de suporte). Num serviço separado, esses dois viram ou replicação de dados ou um cliente HTTP de volta ao monólito — ou seja, o serviço "independente" faz uma chamada de rede para saber de quem é a credencial que acabou de carregar. **Isso é acoplamento distribuído, que é pior que acoplamento local: mesma dependência, agora com latência e modo de falha.**

**Contra a regra do CLAUDE.md.** A regra ("NUNCA microserviços a menos que explicitamente necessário; default Modular Monolith Hexagonal") não é uma preferência estética — no contexto Local-First dela, cada serviço central adicional é mais uma coisa que pode estar fora do ar quando a recepção precisa autorizar um procedimento. A pergunta obrigatória "como isso se comporta se o servidor central cair?" tem uma resposta pior com dois serviços do que com um. Nada nesta análise atinge o patamar de "explicitamente necessário".

### Gatilhos concretos que mudariam esta resposta

Separar quando **dois** destes três forem verdade simultaneamente (um só não basta):

1. **Equipe dedicada:** 2+ engenheiros cujo trabalho é integração de operadoras em tempo integral, e cujos merges no backoffice começam a conflitar com o resto do time. *Fronteira de repositório é fronteira de equipe — este é o gatilho legítimo e é sempre organizacional, não técnico.*
2. **Volume que exige isolamento de falha:** um incidente real em que SOAP lento de operadora degradou requisições **não-TISS** do backoffice, e o isolamento por worker/pool não bastou. *Mede-se em incidente, não em previsão.*
3. **Cliente externo:** uma segunda aplicação (não o SyncroHealth) consumindo a integração TISS — aí ela precisa de contrato versionado de verdade, e o repo separado se paga.

Reavaliar quando cruzarmos **~15 operadoras integradas** ou **~200 clínicas ativas** — não para separar automaticamente, mas para reler estes três gatilhos com dados.

---

## 6. As 5 invariantes que mantêm a extração barata

Não separar agora só é seguro se a opção continuar barata. Estas cinco devem ser verdade em `tiss/` **hoje** e verificadas em CI:

1. **Nenhum outro app importa `tiss`.** Verdade hoje. Vira teste: grep bloqueante no CI. É a invariante mais valiosa das cinco — quebrar ela é o que torna extrações caras, e quebra-se sem querer.
2. **O núcleo (`providers/`, `operadoras/`, builders, clients) não importa nenhum app Django além de `..models`.** Verdade hoje em `providers/`. Vira teste sobre a árvore de imports.
3. **Nenhum import de nome privado cross-app.** Falso hoje: `edge_client.py` importa `clinics.services._is_safe_url`. Corrigir (§4).
4. **Fronteira de entrada única e explícita:** `tiss/views.py` (do Edge Gateway) e `tiss/admin.py` (do suporte). Nenhum outro caminho entra no módulo. Verdade hoje.
5. **Fronteira de saída única e explícita:** `tiss/edge_client.py` e os providers. Nenhuma outra chamada de rede sai do módulo. Verdade hoje.

Com as cinco verdes, a extração futura é: mover a pasta, trocar `Clinic` por um `clinic_id` + client HTTP, publicar. Semanas, não trimestres — e feita com informação que hoje não temos.

---

## 7. O que fazer com `feat/tiss-provider-architecture` (commit `cb6ff42`)

### **Não pausar. Não descartar. Mesclar e complementar.**

Custo de descartar: ~2.700 linhas incluindo 800 de teste, mais dois bugs reais de produção já corrigidos (`ativo` ignorado; lote de clínica Orizon indo para o endpoint genérico). Descartar reintroduz os bugs.

Valor do que ela entrega **sob a premissa corrigida:** ela criou a única coisa que torna a correção barata — um **ponto único de despacho**. Sem `resolve()`, a correção de §3.3 exigiria caçar `if` espalhados por `services.py`. Com ele, é uma mudança dentro de uma fronteira.

O que a premissa errada de fato causou é **menor do que parece**: `providers/orizon.py` é hoje transporte quase puro (envelope, auth, SOAP, normalização de resposta) — que é exatamente o que ele deve ser no desenho corrigido. Ele ainda **não** acumulou nenhuma regra de operadora, porque nenhuma foi implementada. A premissa estava errada no documento; o código ainda não pagou por ela.

**Recomendação prática, nesta ordem:**

1. **Mesclar `feat/tiss-provider-architecture` como está**, após review normal. Sem esperar por este documento.
2. **Abrir imediatamente uma task de guarda** — antes que qualquer particularidade de operadora seja implementada: registrar em `providers/base.py` que providers são **transportes** e que particularidade de operadora **não entra ali**. Documentação + um teste que falha se `registro_ans` literal aparecer em `providers/*.py`. Barato, e é o que impede a dívida de nascer.
3. **Task `TISSOperatorConnection`** (§2) — separar credencial de operadora. Maior valor por esforço do documento inteiro, e é risco de continuidade de faturamento, não refactor estético.
4. **Task `tiss/operadoras/`** (§3.3) — criar com `padrao_ans.py` e **um** módulo real, Bradesco, quando a primeira clínica-piloto confirmar a operadora. Não antecipar os outros cinco: a fronteira se valida com um caso real, não com seis especulativos.
5. **Task das invariantes** (§6) — os greps bloqueantes no CI. É o que compra o direito de adiar a decisão de separar.

Ordem importa: (2) antes de qualquer implementação de particularidade; (3) e (4) podem ir em paralelo; (5) a qualquer momento.

---

## 8. Governança

O que deve estar no CI para que estas decisões não apodreçam:

- **Grep bloqueante:** nenhum app fora de `tiss/` importa `tiss` (invariante 1).
- **Grep bloqueante:** nenhum `registro_ans` literal (`'005711'`, `'421715'`, …) em `tiss/providers/*.py` — particularidade de operadora não mora no transporte.
- **Teste de integridade existente** (toda entrada de `TISSGatewayProvider` registrada em `_PROVIDERS`): manter, e estender para o registro de operadoras quando `tiss/operadoras/` existir.
- **Teste anti-vazamento parametrizado** (nenhum provider loga XML completo): manter bloqueante — é a garantia LGPD do módulo e a única que escala para N operadoras sem review manual.
- **Teste de unicidade de credencial:** depois de §2, garantir que duas `TISSOperatorConfig` da mesma clínica com o mesmo transporte apontem para a **mesma** `TISSOperatorConnection`.
- **Migração de dados de §2:** deve ser reversível e testada com uma clínica de 5 operadoras Orizon no fixture — é o caso que ela existe para resolver.
