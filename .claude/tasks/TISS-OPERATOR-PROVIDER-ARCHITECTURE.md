# Arquitetura de operadoras TISS plugáveis

**Data:** 2026-07-28
**Autor:** Principal Architect (pesquisa + design, a pedido do Tech Lead)
**Status:** documento de design — **nenhum código escrito**, nenhuma decisão implementada
**Repositório:** `syncro_health_backoffice` (a lógica TISS vive aqui, ver §1)
**Relacionado:** `BACFF-014` (`SyncroHealth/.claude/tasks/BACKOFFICE-TASKS-AVULSAS.md`), `EDGW-041` (mTLS/certificado, `SyncroHealth/.claude/tasks/GATEWAY-TASKS.md`), `ADMIN-DASHBOARD-REDESIGN.md` §4.1 (consumidor do health check)

Pedido do Tech Lead: *"Cada operadora tem o seu próprio webservice (SOAP). Hoje temos conhecimento só da Orizon implementada. Devemos pensar em um jeito de plugar/desplugar serviços facilmente."*

---

## 0. Veredito em 6 linhas

1. **A interface plugável vive no Django (`syncro_backoffice/tiss/`), não no Go.** O gateway já é agnóstico de operadora e deve continuar assim.
2. **O ponto de extensão já existe e está meio construído** (`TISSOperatorConfig.gateway_provider` + despacho em `services.py`). Não há reescrita a fazer — há três buracos concretos a fechar.
3. **Reaproveitável entre operadoras: ~40%, não 80%.** O XML da guia é comum (XSD ANS); envelope, autenticação, versão do padrão e regras de negativa **não são**.
4. **"Desplugar" hoje não funciona:** `TISSOperatorConfig.ativo` existe no model e **nunca é consultado** em nenhum caminho de código. É o item de maior valor/menor esforço deste documento.
5. **Health check genérico é viável e deve ser feito agora**, com uma tabela só (`OperatorCallLog`, já desenhada no `ADMIN-DASHBOARD-REDESIGN.md` §4.1) — é o que dá "operadora X está saudável?" uniforme para qualquer provider.
6. **Não criar `OperadoraGateway` como ABC/Protocol formal ainda.** Com 1 operadora real, o `dict` de despacho basta. O gatilho para formalizar está definido em §6.

---

## 1. Onde a lógica TISS realmente vive (confirmado por leitura de código)

**A nota registrada em `EDGW-041` continua correta.** Confirmado em 2026-07-28:

| Camada | Arquivo | Papel |
|---|---|---|
| Gateway Go (clínica) | `syncro_gateway/internal/adapters/output/backoffice/elegibilidade_client.go` | Cliente **HTTP/JSON** para o Backoffice. Autentica por `X-License-Key`. **Não conhece SOAP, WSDL, credencial de operadora nem provider.** Só passa `registro_ans` como chave de negócio. |
| Backoffice Django | `tiss/services.py` | Canal único que fala SOAP com a operadora. Faz o despacho por provider. |
| Backoffice Django | `tiss/soap_client.py` | Transporte SOAP genérico ANS (elegibilidade + envio de lote). |
| Backoffice Django | `tiss/orizon_autorize_client.py` | Transporte SOAP do Autorize Orizon. |
| Backoffice Django | `tiss/xml_builder.py` / `tiss/orizon_autorize_xml_builder.py` | Montagem de XML — genérico ANS 4.02.00 vs Orizon 4.01.00. |
| Backoffice Django | `tiss/models.py::TISSOperatorConfig` | Credenciais/endpoint/provider por (clínica, operadora). |

Fluxo real:

```
Gateway Go ──HTTP+license_key──► Django /api/tiss/elegibilidade/verificar/
                                     │
                                     ▼
                          services.consultar_elegibilidade_automatica
                                     │  switch gateway_provider
                        ┌────────────┴────────────┐
                        ▼                         ▼
              soap_client.py              orizon_autorize_client.py
              (genérico ANS)              (Autorize Orizon)
                        └────────► operadora SOAP ◄────────┘
```

**Veredito:** a interface plugável vive **inteiramente no Django**. O `elegibilidade_client.go` é o contrato certo e não deve ganhar nenhum conceito de "provider" — se o gateway souber qual operadora fala qual dialeto, cada clínica passa a ter que ser atualizada para suportar uma operadora nova, o que mata a premissa de "plugar sem redeploy do parque". A única coisa que o gateway precisa saber é o `registro_ans`, que ele já tem localmente (`domain.InsuranceOperator.ANSCode`).

**Corolário para `EDGW-041` (mTLS):** com esta topologia confirmada, o certificado digital deve viver no **Backoffice**, não no gateway. Um certificado por operadora, num servidor controlado, rotacionável sem tocar em clínica nenhuma. Só trazer para o gateway se uma operadora exigir que a origem TLS seja o IP/CNPJ da própria clínica — nenhuma exige hoje.

---

## 2. Estado atual: o que já é plugável e o que não é

### 2.1 Já existe (feito em BACFF-014, 2026-07-24)

- `TISSOperatorConfig.gateway_provider` — `TextChoices` com `generico_ans` (default) e `orizon` (migration `0007`).
- Despacho em `services.consultar_elegibilidade_automatica`: `if provider == ORIZON: ... else: generico`.
- Normalização de resposta: os dois caminhos devolvem `ElegibilidadeRespostaCompleta`. **Este é o ativo arquitetural mais importante que já temos** — é o "port de saída" de fato, mesmo sem estar declarado como interface.
- Tradução de vocabulário de mock entre providers (`_ORIZON_MOCK_SCENARIO_MAP`) — o chamador não precisa saber qual client roda por baixo.
- Arquivos separados por operadora (`<operadora>_client.py` + `<operadora>_xml_builder.py`) — convenção já estabelecida.

### 2.2 Os buracos concretos (achados por leitura de código, 2026-07-28)

| # | Buraco | Evidência | Gravidade |
|---|---|---|---|
| **B1** | **`ativo` nunca é consultado.** O campo existe (`models.py:48`) e há índice `(clinic, ativo)`, mas os dois lookups do fluxo real — `views.py:214` e `views.py:323` — fazem `TISSOperatorConfig.objects.get(registro_ans=..., clinic=...)` **sem `ativo=True`**. Desativar uma operadora no admin hoje não desativa nada. | `grep -n ativo tiss/*.py` → só model e admin | **Alta** — é literalmente o "desplugar" pedido, e ele não funciona |
| **B2** | **Despacho existe só para elegibilidade.** `services.enviar_lote` chama `soap_enviar_lote` (genérico ANS) **incondicionalmente**, mesmo quando `gateway_provider == orizon`. Uma clínica Orizon consulta elegibilidade pelo Autorize e envia lote pelo endpoint genérico errado. | `services.py:116` | **Alta** — já é bug latente hoje, não é dívida futura |
| **B3** | **Versão do padrão TISS é constante de módulo, não config.** `xml_builder.TISS_PADRAO_VERSAO` = 4.02.00 e o builder Orizon = 4.01.00, ambos fixos no código. O próprio `BACFF-014` já registrou que isso "deveria ser configurável por operadora". | `xml_builder.py:145` (`<Padrao>{TISS_PADRAO_VERSAO}</Padrao>`) | Média |
| **B4** | **O client genérico ANS não envia credencial nenhuma.** `soap_client.verificar_elegibilidade`/`enviar_lote` só mandam `Content-Type` e `SOAPAction`. `login_encrypted`/`senha_encrypted` só são lidos pelo builder da Orizon (`orizon_autorize_xml_builder.py:145-151`). Qualquer operadora direta (SulAmérica/Porto) vai falhar autenticação no caminho "genérico". | `soap_client.py:336-341` | Média — vira Alta no dia da 2ª operadora |
| **B5** | **Zero observabilidade por operadora.** Nenhuma latência, contador ou outcome persistido. O único rastro é `TISSElegibilidadeConsulta`, só para elegibilidade e sem latência. Não há como responder "a operadora X está saudável?". | `ADMIN-DASHBOARD-REDESIGN.md` §4.1 | Média |
| **B6** | **`gateway_provider` é editável livremente no admin.** Nada impede setar `orizon` numa config cujo `endpoint_url` aponta para outro lugar, ou setar um provider sem credenciais. Falha só aparece em runtime, numa consulta de paciente real na recepção. | `admin.py:18-19` | Baixa |

---

## 3. Pesquisa: o quanto do TISS é realmente comum entre operadoras?

### 3.1 O que a ANS padroniza de fato

A ANS publica os WSDLs e XSDs oficiais (temos a cópia em `SyncroHealth/reference/docs_ans/.../Padrão TISS Comunicacao 040300/`): `tissVerificaElegibilidade`, `tissSolicitacaoProcedimento`, `tissLoteGuias`, `tissCancelaGuia`, `tissSolicitacaoStatusAutorizacao`, `tissSolicitacaoStatusProtocolo`, `tissEnvioDocumentos`, `tissSolicitacaoDemonstrativoRetorno`, `tissComunicacaoBeneficiario`, `tissSolicitacaoStatusRecursoGlosa` + os XSDs `tissGuias`/`tissComplexTypes`/`tissSimpleTypes`.

Portanto **é fato, não inferência**, que o conteúdo da guia (beneficiário, prestador, procedimentos TUSS, valores, glosas) tem estrutura comum e credenciamento obrigatório na ANS.

### 3.2 O que a padronização NÃO cobre — confirmado no nosso próprio histórico

O ponto cego já está documentado no `BACFF-014` e é confirmado pelo código:

1. **Versão do padrão não é sincronizada entre operadoras.** Orizon: 4.01.00 → 4.03.00. Nosso builder genérico: 4.02.00. Uma operadora pode aceitar 3 versões, outra só uma. Isso muda namespace e campos obrigatórios.
2. **O envelope de operação diverge.** Padrão genérico: `<mensagemTISS>` + `<epilogo>` dentro do wrapper da operação. Orizon (Autorize e Fature): `cabecalho`/payload/`hash` como irmãos diretos dentro de um wrapper terminado em `WS` (`solicitacaoProcedimentoWS`, `loteGuiasWS`), **sem** `mensagemTISS`/`epilogo`. Isso não é detalhe cosmético — é código de montagem diferente.
3. **Autenticação está fora do payload TISS.** Orizon: `loginSenhaPrestador` com MD5 no `<cabecalho>` (certificado opcional). Genérico ANS: nada. SulAmérica/Porto diretas podem exigir WS-Security, mTLS, token, ou um esquema próprio. **Nenhum grama disso é coberto por "payload único".**
4. **Composição de operações difere.** A Orizon **não tem** `tissVerificaElegibilidade` isolada — a elegibilidade vem embutida em `solicitacaoProcedimento`, com estado "Em Análise" que exige polling ≥30min via `solicitacaoStatusAutorizacao`. Uma operadora direta provavelmente **tem** a operação isolada. Ou seja: a mesma pergunta de negócio ("esse paciente está coberto?") mapeia para operações SOAP diferentes por operadora.
5. **Particularidades de negócio por operadora** (Cap. 8 do manual Orizon, já lido): Bradesco tem 2 registros ANS distintos (`005711` vs `421715` — errar gera negativa automática) + Solicitação de Senha prévia (`tipoEtapaAutorizacao=1`) com auto-cancelamento em 12h; Cabesp/Cassi exigem sequencial de endereço com dígitos fixos após a matrícula; Bradesco usa "Código Centralizador" no `codigoPrestadorNaOperadora` onde Cassi/Cabesp usam o sequencial; Economus/Careplus/Seguros Unimed têm regras próprias de negativa.
6. **Homologação é sempre por operadora**, independente de o payload ser idêntico.

### 3.3 Veredito quantitativo

| Camada | Reaproveitável? | Onde vive hoje |
|---|---|---|
| Estrutura da guia / procedimentos TUSS / valores | ✅ **Sim, ~100%** — é o XSD ANS | `xml_builder._guia_sp_sadt_xml` |
| Validação XSD do XML montado | ✅ Sim (parametrizando o XSD por versão) | `xml_validator.py` |
| Normalização de resposta para o domínio | ✅ Sim — já é feito | `ElegibilidadeRespostaCompleta` |
| Log/observabilidade/retenção | ✅ Sim | (não existe — B5) |
| Cabeçalho / envelope / wrapper de operação | ❌ **Não** — diverge por operadora | `_cabecalho_transacao_xml` vs `_cabecalho_xml` |
| Autenticação/transporte | ❌ **Não** | espalhado |
| Versão do padrão | ❌ Não (é config, hoje é constante) | constante de módulo |
| Mapeamento operação de negócio → operação SOAP | ❌ Não | `services.py` |
| Regras de negativa/glosa e campos proprietários | ❌ Não | não implementado |

**Números honestos: ~40% de reaproveitamento real, concentrado no miolo do XML da guia.** A promessa de "payload único serve todas" é verdadeira para o miolo e falsa para tudo que o embrulha — e o que embrulha é a fatia maior do esforço de integração. Isso já está confirmado empiricamente: o Autorize da Orizon exigiu um client **e** um builder novos inteiros, mesmo com o payload da guia sendo padrão ANS.

**Implicação de design:** a fronteira certa de reuso **não é** "um client genérico com hooks". É **um builder de guia compartilhado + um provider por operadora que resolve envelope, auth e particularidades**. É exatamente a convenção que o código já adotou por acidente feliz — vale torná-la explícita.

---

## 4. O desenho proposto

### 4.1 Contrato (o "port")

Um único ponto de despacho por operação de negócio, com assinatura estável e retorno já normalizado. **Em Python, começar como convenção documentada + registro em `dict`** — não como ABC/Protocol formal (ver §6 para o gatilho de formalização).

```
tiss/providers/__init__.py     ← registro + resolução (o ÚNICO switch do sistema)
tiss/providers/base.py         ← docstring do contrato + dataclasses de retorno
tiss/providers/generico_ans.py ← wrapper fino sobre soap_client.py + xml_builder.py
tiss/providers/orizon.py       ← wrapper fino sobre orizon_autorize_client.py + builder
```

Contrato (4 funções, nada além disso no MVP):

| Função | Entrada | Retorno | Nota |
|---|---|---|---|
| `verificar_elegibilidade(clinic, operator_config, numero_carteira, ...)` | dados de negócio | `ElegibilidadeRespostaCompleta` | já existe, só muda de lugar |
| `enviar_lote(lote, guias, ...)` | dados de negócio | `EnvioLoteResultado` | **fecha B2** |
| `health_check(operator_config)` | config | `ProviderHealth` | ver §4.4 |
| `capabilities()` | — | `ProviderCapabilities` | ver §4.5 |

Resolução:

```python
_PROVIDERS = {
    TISSGatewayProvider.GENERICO_ANS: generico_ans,
    TISSGatewayProvider.ORIZON: orizon,
}

def resolve(operator_config):
    if not operator_config.ativo:
        raise OperadoraDesativada(operator_config.registro_ans)
    try:
        return _PROVIDERS[operator_config.gateway_provider]
    except KeyError:
        raise ProviderNaoRegistrado(operator_config.gateway_provider)
```

**Trade-off explícito.** O que ganhamos: um único lugar que sabe "qual código roda para qual operadora", em vez de um `if` por operação espalhado em `services.py` (que já produziu B2). O que custa: uma pasta e ~60 linhas de indireção. Com 2 clients já existentes e um 3º anunciado, o custo se paga na primeira operação nova. **O que explicitamente NÃO fazemos:** nada de `entry_points`/plugin dinâmico, nada de carregamento por string, nada de hot-reload, nada de ABC com 12 métodos abstratos. O conjunto de providers é conhecido em tempo de deploy — um `dict` é a estrutura correta e final.

### 4.2 Como "desplugar" funciona na prática

Há **dois níveis distintos** de desplugar, e confundi-los é o erro mais provável aqui:

**Nível 1 — desativar uma operadora para UMA clínica** (`TISSOperatorConfig.ativo = False`). É o caso comum: contrato encerrado, credencial revogada, operadora fora do ar por dias.

- `resolve()` levanta `OperadoraDesativada`; as views devolvem **`409 Conflict`** com `{"error": "operadora_desativada"}` — deliberadamente **não** `404`, para o gateway poder distinguir "não cadastrada" de "cadastrada e desligada" e mostrar mensagem certa na recepção.
- **O que acontece com quem já usa:** nada é apagado. `TISSLote`/`TISSGuia`/`TISSElegibilidadeConsulta` têm `on_delete=PROTECT` e continuam consultáveis. Só o caminho de **saída** (chamar a operadora) é fechado.
- **O caminho manual continua aberto.** `registrar_elegibilidade_manual` não fala com operadora nenhuma — é a recepcionista registrando o que obteve por telefone/portal. **Ele deve continuar funcionando com a operadora desativada**, e essa é a razão pela qual "desplugar" é seguro do ponto de vista de continuidade operacional da clínica: a clínica não para, degrada para o fluxo manual que já é fallback de primeira classe. **Isso é uma decisão de produto, não técnica — precisa de aval do Tech Lead (ver §7, D2).**
- Lotes com `status=enviando` no momento da desativação: não são cancelados automaticamente. Ficam consultáveis e a consulta de status é bloqueada junto. Alternativa (marcar como `erro_envio`) é pior — perde-se o protocolo já obtido.

**Nível 2 — remover um provider do código** (tirar `orizon` de `_PROVIDERS`). Raro, e deve **falhar alto**: `resolve()` levanta `ProviderNaoRegistrado`, com um check de integridade no CI (§8) que quebra o build se existir `TISSOperatorConfig` ativo apontando para provider inexistente. Nunca fazer fallback silencioso para `generico_ans` — mandar payload no dialeto errado para uma operadora real gera glosa e retrabalho de faturamento na clínica, um custo bem pior que um erro explícito.

### 4.3 Onde a autenticação entra

Manter o desenho já registrado em `EDGW-041`, agora com um lugar concreto: o provider é dono da autenticação. `orizon.py` sabe que auth é `loginSenhaPrestador` com MD5 no cabeçalho; `sulamerica.py`, quando existir, saberá o que for o caso dela. O `TransportAuthenticator` do `EDGW-041` (`Client() → http.Client` já configurado) é a forma correta **se e quando** duas operadoras compartilharem mecanismo de transporte — com uma só, é indireção prematura. `TISSOperatorConfig` ganha, quando necessário, os campos de certificado (`cert_pfx_encrypted`, `cert_senha_encrypted`, `cert_valido_ate`) seguindo o mesmo padrão Fernet já usado em `login_encrypted`/`senha_encrypted`.

**LGPD/segurança — não negociável, vale para qualquer provider novo:** credencial nunca em texto plano no banco, nunca em `__str__`, nunca em `list_display`, nunca em serializer, nunca em log. Nenhum provider pode logar XML de request/response completo (contém nome e carteirinha do beneficiário) — só protocolo, código de erro e metadado não sensível. O código atual respeita isso; o checklist de §5 tem esse item como bloqueante.

### 4.4 Health check genérico por operadora

Duas partes, deliberadamente separadas:

**(a) Passivo — a fonte de verdade.** Adotar `tiss.OperatorCallLog` exatamente como desenhado no `ADMIN-DASHBOARD-REDESIGN.md` §4.1 (chave de agregação `registro_ans` + `gateway_provider`, `operation`, `outcome`, `latency_ms`, sem payload nem `erro_mensagem` cru por LGPD, purga em 90 dias). **A única mudança que este documento propõe:** a escrita não fica dentro de `soap_client.py` (isso obrigaria cada provider novo a lembrar de instrumentar), e sim **dentro de `providers/__init__.py`, envolvendo a chamada ao provider resolvido**. Assim um provider novo ganha observabilidade de graça, sem uma linha de instrumentação própria. Isso é o que torna o health check *genérico* de verdade em vez de "genérico se o dev lembrar".

**(b) Ativo — `health_check()` no contrato.** Chamada barata e explicitamente sob demanda (botão "Testar conexão" no admin da config da operadora), retornando `ProviderHealth(reachable, latency_ms, detail)`. Para o genérico ANS: `GET` no `?wsdl` ou um `HEAD` no endpoint. Para a Orizon: idem — **nunca** uma solicitação de autorização real, que tem custo contratual e usa dados de paciente.

**Não implementar probe ativo periódico (Celery Beat) no MVP.** Concordo com a recomendação já registrada no `ADMIN-DASHBOARD-REDESIGN.md`: exige credencial de clínica-cliente para monitoramento nosso, o que é problema de LGPD e de contrato, e pode esbarrar em rate limit/cobrança por consulta. Aceitar ⚪ "sem tráfego" como estado neutro com janela de 24h.

O dashboard pergunta uma coisa só, para qualquer operadora plugada: `estado(registro_ans, janela)` → 🟢/🟡/🔴/⚪, com as regras de classificação já definidas no §4.1 daquele documento. Nenhum `if orizon` no dashboard, hoje ou nunca.

### 4.5 `capabilities()` — o que resolve o problema real da UI

Este é o item menos óbvio e o de maior valor a médio prazo. A Orizon **não tem** elegibilidade isolada; uma operadora direta provavelmente tem. Sem um jeito de perguntar isso, a UI da recepção vai acabar com `if operadora == 'orizon'` no frontend — exatamente o hardcode que queremos evitar, só que num lugar pior (no parque de clínicas, onde atualizar é caro).

```python
ProviderCapabilities(
    elegibilidade_isolada: bool,      # False na Orizon (vem embutida na autorização)
    autorizacao_procedimento: bool,
    envio_lote: bool,
    consulta_status: bool,            # polling (Orizon "Em Análise")
    cancelamento_guia: bool,
    versoes_padrao_suportadas: list,  # ex. ['4.01.00','4.03.00'] — resolve B3
)
```

Exposto no serializer de `TISSOperatorConfig` (já consumido pelo gateway). O gateway e a UI passam a habilitar botões por capability, não por nome de operadora.

---

## 5. Onboarding de uma operadora nova — checklist concreto

Para adicionar SulAmérica/Porto/Mediservice depois do desenho acima. **Passos 1-2 são bloqueantes e não são código** — a lição mais cara do `BACFF-014` foi tentar codar contra o WSDL genérico da ANS presumindo que serviria.

**Fase 0 — pré-código (bloqueante)**
- [ ] Manual técnico **oficial** da operadora em mãos (não WSDL genérico ANS, não PDF de terceiro). Registrar a fonte e a data no board.
- [ ] Acesso a homologação confirmado, com o pré-requisito de credenciamento resolvido (a SyncroHealth é fornecedora, não prestadora — depende de clínica-cliente credenciada; mesmo bloqueio já vivido com a Orizon).
- [ ] Registrar por escrito: versão(ões) do padrão aceitas, mecanismo de auth, forma do envelope, quais operações existem, particularidades de negócio conhecidas.

**Fase 1 — código (~1 dia útil se a Fase 0 estiver completa)**
- [ ] Novo valor em `TISSGatewayProvider` + migration.
- [ ] `tiss/<operadora>_client.py` — transporte + parsing, com mocks por cenário (`TISS_SOAP_MOCK`), no padrão de `orizon_autorize_client.py`.
- [ ] `tiss/<operadora>_xml_builder.py` — **reaproveitar `xml_builder._guia_sp_sadt_xml` para o miolo da guia**; escrever só cabeçalho/envelope/auth próprios.
- [ ] `tiss/providers/<operadora>.py` — implementa as 4 funções do contrato, normalizando para `ElegibilidadeRespostaCompleta`/`EnvioLoteResultado`. Declara `capabilities()`.
- [ ] Registrar em `_PROVIDERS`.

**Fase 2 — validação (bloqueante para produção)**
- [ ] Testes de contrato do provider (§8) verdes.
- [ ] Validação XSD do XML gerado contra a versão do padrão **daquela** operadora.
- [ ] Homologação ponta a ponta contra o ambiente da operadora, com evidência arquivada.
- [ ] Revisão de segurança: nenhum log de XML completo, credencial só via Fernet, `capabilities()` bate com o manual.
- [ ] Só então marcar `ativo=True` em produção.

**O que NÃO deve ser necessário para plugar uma operadora nova:** tocar em `services.py`, tocar no gateway Go, tocar no frontend Wails/React, tocar no dashboard de saúde. Se algum desses for necessário, o desenho vazou e deve ser corrigido antes de mergear — **este é o teste de aceite do design inteiro.**

---

## 6. MVP-viável agora vs. esperar a 2ª operadora

Regra que aplico aqui: com **uma** operadora real, generalizar é especulação. Mas os itens abaixo se pagam **hoje**, com uma operadora — ou são bugs, ou são coisas que ficam mais caras quanto mais tarde forem feitas.

### Faça agora (baixo esforço, alto valor, justificado com 1 operadora)

| Item | Por quê agora | Esforço |
|---|---|---|
| **B1 — `ativo` passa a ser respeitado** + `409 operadora_desativada` | É o "desplugar" pedido, e ele simplesmente não funciona hoje. É bug, não feature. | ~1h |
| **B2 — despachar `enviar_lote` por provider** | Bug latente: clínica Orizon envia lote pelo endpoint genérico errado. | ~1h |
| **`tiss/providers/` + `resolve()`** | Move 2 despachos existentes para um lugar só. Sem isso, cada operação nova repete o erro do B2. | ~2h |
| **`OperatorCallLog` + instrumentação no `resolve()`** | Hoje não sabemos se a Orizon está de pé. Vale com 1 operadora; instrumentar no ponto de despacho torna gratuito para as próximas. | ~3h |
| **B3 — versão do padrão vira campo de `TISSOperatorConfig`** | A Orizon **sozinha** já precisa (4.01→4.03, e aceita várias). Não é preparação para operadora hipotética. | ~2h |
| **B6 — `clean()` valida provider × credenciais × endpoint** | Barato, e evita descobrir config quebrada numa consulta de paciente real na recepção. | ~1h |

Total: **~10h**, e nenhum desses itens é justificado por "operadora futura" — todos se sustentam com a Orizon sozinha.

### Espere a 2ª operadora real confirmada

| Item | Gatilho |
|---|---|
| ABC/`Protocol` formal para o provider | Quando existirem **3** providers, ou quando um dev externo à sessão for escrever um. Com 2, a docstring do contrato + testes de contrato compartilhados dão a mesma garantia sem a cerimônia. |
| `TransportAuthenticator` (`EDGW-041`) | Quando 2 operadoras compartilharem mecanismo de transporte, ou na primeira que exigir mTLS de fato. |
| `capabilities()` exposto no serializer e consumido pela UI | Quando a 2ª operadora tiver composição de operações diferente. **Mas defini-lo já em §4.5 é barato** — deixar declarado desde o início evita o `if orizon` no frontend, que é o hardcode mais caro de desfazer (está no parque de clínicas). |
| Particularidades Bradesco/Cabesp/Cassi/etc. | Quando a clínica-piloto revelar qual usa de fato. Incremental, dentro do provider correspondente. |
| Probe ativo periódico | Quando tivermos credencial de **homologação própria** — nunca com credencial de cliente. |
| Registry dinâmico / plugin por entry_point | **Nunca**, salvo integrador externo escrevendo providers, o que não está no roadmap. |

---

## 7. Decisões que precisam do Tech Lead antes de qualquer código

**D1 — Elegibilidade vs. Autorização continuam sendo dois conceitos? DECIDIDO (2026-07-28): unificar.**
Tech Lead confirmou a recomendação da PO Healthtech: `verificar_elegibilidade` e `solicitar_autorizacao` se unificam num conceito só no contrato do provider. `capabilities().elegibilidade_isolada` deixa de existir. Implementar `providers/base.py` já com o contrato unificado desde o início.

**D2 — Com a operadora desativada, o registro manual continua permitido? DECIDIDO (2026-07-28): sim.**
Confirmado: cadastro/registro manual (`registrar_elegibilidade_manual`) continua funcionando normalmente com a operadora desativada — só a chamada automática/SOAP é bloqueada. Mesmo padrão já aplicado no hotfix BACFF-AVULSA-12/13 (PR #48).

**D3 — Investimento no caminho `generico_ans` sem operadora que o use. DECIDIDO (2026-07-28): rebaixar para falha explícita (opção b).**
Confirmado: `generico_ans` deixa de ser o default silencioso. Vira `desconhecido` (ou equivalente), que falha explicitamente em vez de tentar um dialeto não confirmado por nenhuma operadora real. Requer migration de dados nas configs `TISSOperatorConfig` existentes que hoje apontam pro genérico sem confirmação real de compatibilidade.

**D4 — Ordem de prioridade entre este trabalho e a integração Fature. DECIDIDO (2026-07-28): providers primeiro.**
Confirmado: implementar `providers/` (arquitetura plugável) antes de `orizon_fature_client.py` (Fature, P0 do BACFF-014), evitando mover `services.enviar_lote` duas vezes. Fature aguarda a conclusão desta arquitetura.

**D5 — Confirmar o corolário de `EDGW-041`. PENDENTE — Tech Lead quer revisar antes de decidir.**
Não fechar `EDGW-041` ainda. A topologia proposta (certificado digital vive no Backoffice, não no gateway) permanece como recomendação do documento, não decisão confirmada. Não implementar nada que dependa dessa decisão até ela ser fechada explicitamente.

---

## 8. Governança — o que o CI/CD precisa garantir

Sem isto, o padrão apodrece na terceira operadora.

1. **Suite de testes de contrato compartilhada.** Um módulo parametrizado sobre `_PROVIDERS` que roda os mesmos casos contra **todo** provider registrado: retorno normalizado no tipo certo, falha de rede vira o resultado esperado (não exception vazando), `health_check` não levanta, `capabilities()` completo. Provider novo sem passar = build vermelho. **É isto que substitui a ABC formal, e é o que permite adiá-la com segurança.**
2. **Teste de integridade de configuração.** Nenhum `TISSOperatorConfig` com `ativo=True` apontando para provider fora de `_PROVIDERS`; nenhum provider que exija credencial com `login_encrypted`/`senha_encrypted` vazios.
3. **Teste anti-vazamento de PII.** Capturar os logs durante uma chamada mockada de cada provider e afirmar que nenhuma linha contém `numero_carteira`, `beneficiario_nome` ou XML completo. **Marcar como bloqueante de merge** — é o item de LGPD que mais facilmente regride quando alguém adiciona um `logger.debug` para depurar uma operadora nova.
4. **Lint arquitetural.** `grep` de CI proibindo `orizon` (case-insensitive) fora de `tiss/orizon_*.py` e `tiss/providers/orizon.py`. Mesma regra para cada operadora futura. É o guarda de "não hardcode a operadora" mais barato que existe.
5. **Teste de "desplugar".** Desativar a config e afirmar: elegibilidade automática → 409; envio de lote → 409; lotes/guias históricos continuam legíveis; registro manual segue o que for decidido em D2.
6. **Migration de `gateway_provider` sempre acompanhada de provider registrado.** Adicionar um valor ao `TextChoices` sem entrada em `_PROVIDERS` deve quebrar o item 2.
7. **Retenção do `OperatorCallLog`** (purga > 90 dias) com comando de management e teste — senão a tabela cresce sem limite e vira custo de banco no Railway.

---

## 9. Resumo de arquivos

Leitura desta investigação (nada foi modificado):

- `syncro_health_backoffice/tiss/models.py` — `TISSOperatorConfig`, `TISSGatewayProvider`, `ativo`
- `syncro_health_backoffice/tiss/services.py` — despacho atual (elegibilidade só), `enviar_lote` sem despacho (B2)
- `syncro_health_backoffice/tiss/soap_client.py` — transporte genérico ANS, sem credencial (B4)
- `syncro_health_backoffice/tiss/orizon_autorize_client.py` — transporte Autorize Orizon
- `syncro_health_backoffice/tiss/xml_builder.py` — `TISS_PADRAO_VERSAO` constante (B3)
- `syncro_health_backoffice/tiss/orizon_autorize_xml_builder.py` — `loginSenhaPrestador` MD5
- `syncro_health_backoffice/tiss/views.py` — lookups sem `ativo=True` (B1)
- `SyncroHealth/syncro_gateway/internal/adapters/output/backoffice/elegibilidade_client.go` — cliente HTTP agnóstico de operadora (confirma topologia)
- `SyncroHealth/reference/docs_ans/.../Padrão TISS Comunicacao 040300/` — WSDLs/XSDs oficiais ANS

**Este documento não autoriza início de código.** Fecha D1-D5 primeiro.

---

## 10. Status de implementação — CONCLUÍDA (2026-07-28)

**Implementado em:** branch `feat/tiss-provider-architecture` → PR contra `develop`.
**Base:** `origin/develop` @ `8aab9c6` (hotfix BACFF-AVULSA-12/13, PR #48).
**Testes:** suíte completa verde — **700 testes** (653 antes, +47 novos), zero regressão.

Este documento deixa de ser só design: as decisões D1–D4 estão implementadas.
**D5 (certificado digital/mTLS, EDGW-041) permanece NÃO fechado e nada foi
implementado que dependa dele** — ver §10.4.

### 10.1 O que foi entregue

| Item | Onde | Nota |
|---|---|---|
| Contrato do provider | `tiss/providers/base.py` | 4 funções (§4.1), já com **D1** aplicado: elegibilidade+autorização unificadas em `verificar_cobertura`. `elegibilidade_isolada` não existe. |
| Registro + resolução | `tiss/providers/__init__.py` | `_PROVIDERS` + `resolve()` — o único switch do sistema. |
| Provider Orizon | `tiss/providers/orizon.py` | Lógica migrada de `services.py`; `_ORIZON_MOCK_SCENARIO_MAP` saiu de `services` para o provider. |
| Provider genérico ANS | `tiss/providers/generico_ans.py` | Wrapper sobre `soap_client` + `xml_builder`; `_build_pedido_elegibilidade_xml` migrado de `services`. |
| Provider `desconhecido` | `tiss/providers/desconhecido.py` | **D3** — falha explícita, é o novo default do model. |
| Health check ativo | `tiss/providers/health.py` + ação de admin | §4.4(b). Sonda `?wsdl`, nunca autorização real. |
| Health check passivo | `tiss.OperatorCallLog` (migration `0009`) | §4.4(a). Instrumentação **no `resolve()`**, não nos clients. |
| Retenção 90 dias | `manage.py purgar_operator_call_log` | §8.7, com `--dry-run`. |
| `capabilities()` | `providers/base.py` + serializer + admin | §4.5, exposto na API de `TISSOperatorConfig`. |
| Migration de dados D3 | `tiss/migrations/0008_gateway_provider_desconhecido.py` | `generico_ans` → `desconhecido`; Orizon intocada. |
| Governança/CI | `tiss/tests_providers.py` (47 testes) | §8.1–§8.7, incluindo o teste de desplugar e o anti-PII. |

### 10.2 Itens do §6 ("faça agora") — cobertos vs. fora

| Item do §6 | Status |
|---|---|
| **B1** — `ativo` respeitado + `409 operadora_desativada` | ✅ Movido do hotfix (`services._checar_operadora_ativa`) para `providers.resolve()`, onde é impossível uma operação nova esquecer dele. |
| **B2** — despachar `enviar_lote` por provider | ✅ O `if` do hotfix virou propriedade declarada do provider (`orizon.enviar_lote` levanta `OperacaoNaoSuportada`). Código de erro `provider_lote_nao_implementado` preservado — o gateway já o trata. |
| **`tiss/providers/` + `resolve()`** | ✅ |
| **`OperatorCallLog` + instrumentação** | ✅ Implementado (era "~3h extra, não incluído" no `ADMIN-DASHBOARD-REDESIGN.md` §4.1). Escrita no ponto de despacho, não no `soap_client` — divergência deliberada daquele documento, ver §4.4(a). |
| **B3** — versão do padrão vira campo de `TISSOperatorConfig` | ❌ **FORA.** As versões são expostas via `capabilities().versoes_padrao_suportadas` (lidas das constantes dos builders), mas continuam constantes de módulo. Torná-las campo editável por config exige decidir o que acontece quando a versão muda o XSD de validação — merecia sua própria task. |
| **B6** — `clean()` valida provider × credenciais × endpoint | ❌ **FORA.** Parcialmente mitigado pelo D3: o default deixou de ser um provider que tenta falar um dialeto não confirmado, que era o pior caso que o `clean()` pegaria. Validação de "provider `orizon` sem credencial" continua pendente. |

### 10.3 Decisões de implementação que o documento não fixava

1. **`generico_ans` não foi removido, foi rebaixado.** O D3 dizia "vira `desconhecido` (ou equivalente)". Interpretação adotada: `desconhecido` é o novo **default**, e `generico_ans` continua um valor **selecionável deliberadamente**. Motivo: o transporte genérico ANS é código real, testado, e será a base da 1ª operadora direta; apagá-lo custaria retrabalho sem reduzir risco, já que o risco vinha de ele ser *default*, não de ele existir. A migration move todas as configs `generico_ans` existentes para `desconhecido` — como era o default do model, é impossível distinguir escolha de omissão, então todas são tratadas como não confirmadas.

2. **`verificar_cobertura` no provider, `consultar_elegibilidade_automatica` no service.** A função pública de `services` manteve o nome histórico: views, gateway Go e testes já o usam, e renomear seria quebra de contrato sem ganho. A unificação do D1 vale onde importa — no contrato do provider.

3. **`ElegibilidadeRespostaCompleta` mudou de módulo** (`services` → `providers/base`), porque é o tipo de retorno do contrato e a dependência agora aponta de `services` para `providers`. `services` reexporta o nome; nenhum chamador quebrou.

4. **Providers não escrevem no banco.** `ElegibilidadeRespostaCompleta` ganhou `status_operacional` (metadado técnico, não serializado para o gateway) para que `services` saiba qual `TISSElegibilidadeStatus` registrar. Alternativa descartada: o provider chamar o logger direto — acoplaria todo provider novo à persistência.

5. **`409` foi generalizado.** Passou a cobrir `provider_nao_confirmado` e `provider_nao_registrado` além de `operadora_desativada` — todos são "operadora cadastrada, mas a configuração bloqueia a chamada", que é o que o gateway precisa distinguir de `404`.

6. **`health_check` e `capabilities()` funcionam com a operadora desativada.** `ativo=False` fecha a saída de negócio, não a introspecção: o admin precisa testar a conexão *antes* de religar.

7. **Achado do lint arquitetural (§8.4):** `tiss/apps.py` rotulava o app inteiro como `'TISS/SOAP (Orizon)'`. Corrigido para `'TISS/SOAP'` — o app fala com N operadoras.

### 10.4 O que continua fora de escopo

- **D5 / EDGW-041 (certificado digital, mTLS):** nada implementado. Nenhum campo de certificado foi adicionado a `TISSOperatorConfig`. O ponto de extensão está registrado na docstring de `providers/base.py`: como o provider é dono da autenticação, quando a topologia for decidida o provider que precisar de mTLS configura seu transporte sem mudar nenhuma assinatura do contrato.
- **D4 / Orizon Fature:** `orizon_fature_client.py` não foi escrito. `providers/orizon.py::enviar_lote` levanta `OperacaoNaoSuportada` e `capabilities().envio_lote` é `False`. A arquitetura está pronta para recebê-lo: implementar o Fature é preencher uma função de um módulo que já existe.
- **ABC/`Protocol` formal:** adiada conforme §6. A suíte de contrato parametrizada sobre `_PROVIDERS` (`tests_providers.py::ContratoDeProviderTests`) dá a mesma garantia sem a cerimônia. Gatilho para formalizar permanece: 3 providers reais, ou um dev externo escrevendo um.
- **Probe ativo periódico (Celery Beat):** não implementado, conforme §4.4 — exigiria credencial de clínica-cliente para monitoramento nosso.

### 10.5 Achado posterior — a Orizon é um AGREGADOR, não uma operadora

**Registrado em 2026-07-28, depois do código já escrito. Nada foi
implementado a respeito — é nota para o Tech Lead avaliar junto com a
análise do Principal Architect sobre separar a integração de operadoras num
repositório próprio.**

Contexto novo do Tech Lead: a Orizon **não é uma operadora** — é um meio de
comunicação/agregador que já atende múltiplas operadoras reais
(**Bradesco, CarePlus, Cabesp, Cassi, Seguros Unimed**), cada uma com
particularidades próprias *dentro* do que a Orizon expõe.

**O desenho implementado continua correto e funcional como MVP**, mas a
modelagem `1 gateway_provider = 1 dialeto` pode não escalar bem para esse
cenário. O ponto de tensão é concreto e este próprio documento já o
antecipava sem tirar a conclusão: o §3.2 item 5 lista particularidades que
**não são da Orizon, são das operadoras dentro dela** —

- Bradesco: **dois registros ANS distintos** (`005711` vs `421715`, errar
  gera negativa automática) + Solicitação de Senha prévia
  (`tipoEtapaAutorizacao=1`) com auto-cancelamento em 12h;
- Cabesp/Cassi: sequencial de endereço com dígitos fixos após a matrícula;
- Bradesco usa "Código Centralizador" no `codigoPrestadorNaOperadora` onde
  Cassi/Cabesp usam o sequencial;
- Economus/CarePlus/Seguros Unimed: regras próprias de negativa.

E o §6 registra "particularidades Bradesco/Cabesp/Cassi/etc. → incremental,
**dentro do provider correspondente**" — o que, sob a leitura antiga,
significava "dentro de `providers/orizon.py`". Com o contexto novo, isso
empilharia N conjuntos de regras de operadora dentro de um único módulo,
que é exatamente o tipo de `if` por operadora que esta arquitetura existe
para eliminar — só que escondido um nível abaixo.

**Duas direções possíveis para a próxima iteração** (nenhuma decidida, nenhuma
implementada):

1. **Provider por operadora real, com transporte compartilhado.** `bradesco`,
   `cassi`, `cabesp`… cada um um provider, todos reusando um módulo de
   transporte/envelope Orizon comum. Encaixa no contrato atual sem mudá-lo —
   `_PROVIDERS` cresce, `resolve()` não muda. Custo: `TISSGatewayProvider`
   vira uma lista longa e o vínculo "esta operadora fala via Orizon" fica
   implícito no código do provider.
2. **Segundo eixo explícito no modelo:** separar *canal* (Orizon, direto,
   outro hub) de *operadora* (`registro_ans`), com o provider resolvido pelo
   par. Mais fiel à realidade e provavelmente o caminho certo se a meta é
   "integrar a maior quantidade possível de operadoras", mas é mudança de
   modelagem em `TISSOperatorConfig` e merece decisão explícita — não cabia
   nesta rodada.

**O que a arquitetura entregue já ajuda, independente da direção escolhida:**
`resolve()` é o único ponto de despacho, `capabilities()` já descreve
capacidade em vez de identidade, e a chave de agregação do `OperatorCallLog`
já é `registro_ans` — ou seja, a observabilidade **já** distingue Bradesco de
Cassi mesmo com ambas passando pelo provider `orizon` hoje. Nenhuma dessas
peças precisa ser refeita nas duas direções acima.

---

## 11. Fature (envio de lote de faturamento) — arquitetura multi-operadora, decisão fechada (2026-07-30)

**Fecha a tensão em aberto do §10.5** para o eixo de faturamento (o eixo de
autorização/elegibilidade não muda nesta rodada). Pedido do usuário
(2026-07-30): desenhar o Fature (envio de lote TISS) com visão
multi-operadora desde o início — hoje só a Orizon (atende Bradesco,
CarePlus, Cabesp, Cassi, Seguros Unimed), mas amanhã pode ser necessário
falar DIRETO com Sulamérica, Porto Seguro, Unimed, NotreDame etc. Ports and
adapters explícito, sem tratar a Orizon como "a" integração de faturamento
única.

### 11.1 Resolução do §10.5: qual das duas direções vale para o Fature

**A "direção 2" do §10.5 (segundo eixo explícito canal × operadora) já
existe na modelagem, sem precisar de campo novo.** `TISSOperatorConfig` já é
a chave "operadora real" (`registro_ans` + `nome_operadora`, `unique_together
= [('clinic', 'registro_ans')]`) e já tem FK para `TISSOperatorConnection`,
que carrega o `gateway_provider` (o "canal"/transporte). Múltiplas
`TISSOperatorConfig` (uma por `registro_ans` — Bradesco `005711`, Bradesco
`421715`, CarePlus, Cabesp, Cassi, Seguros Unimed) já podem apontar para a
MESMA `TISSOperatorConnection` (mesmo endpoint Orizon + mesma credencial),
via `TISSOperatorConnection.get_or_create_for`. Ou seja: **canal e operadora
real já são dois eixos independentes no schema hoje** — só não estavam
formalizados como tal em prosa. A "direção 1" (provider por operadora real
tipo `bradesco`/`cassi`, todos reusando um módulo de transporte comum) NÃO é
necessária: o transporte comum já é o módulo `providers/orizon.py`
compartilhado, e a diferenciação por operadora real já acontece via
`registro_ans`, sem precisar multiplicar entradas em `TISSGatewayProvider`/
`_PROVIDERS` por operadora.

Isso resolve diretamente o pedido do usuário: **a Orizon só é usada quando o
`TISSOperatorConfig` daquela guia aponta (via `connection`) para
`gateway_provider='orizon'`** — e isso só deveria ser verdade para os
`registro_ans` que a Orizon de fato atende. Uma Sulamérica direta ganha seu
próprio `TISSOperatorConfig` com uma `TISSOperatorConnection` NOVA (endpoint
próprio, `gateway_provider` novo, ex. `sulamerica_direto`) — nunca
reaproveitando a connection Orizon. O mapeamento "quais operadoras a Orizon
atende" nunca é uma lista hardcoded no código: é decorrência de qual
connection cada config aponta, uma decisão de CADASTRO (admin), não de
deploy.

**Não é necessário `fature_provider` distinto de `gateway_provider`.**
Confirmado contra o manual `Fature Integração Tecnica Webservice - TISS
4.03.00.pdf`: a autenticação do Fature usa o MESMO campo XML
`senhaPrestador` do Autorize (a UI do portal chama de "Chave de
Transmissão", mas é o mesmo par login/senha da mesma connection) — ou seja,
autorização e faturamento da MESMA operadora real, pelo MESMO hub, sempre
compartilham a mesma `TISSOperatorConnection`/`gateway_provider`. Um campo
`fature_provider` separado só se justificaria se alguma operadora falasse
autorização por um hub e faturamento por outro — nenhum manual lido até
agora sustenta esse caso, e criar o campo agora seria abstração especulativa
(regra anti-overengineering do projeto). Se esse caso aparecer, estender
`TISSGatewayProvider`/adicionar um campo é migration barata e conhecida — não
vale pagar o custo de manutenção antes de haver evidência real.

**Nenhuma migration de banco é necessária** para o roteamento do Fature —
`gateway_provider`, `registro_ans`, `nome_operadora` já existem e já bastam.

### 11.2 Contrato do port — extensão mínima de `providers/base.py`

O contrato de `providers/base.py` já reserva `enviar_lote(lote, guias,
sequencial_transacao, mock_scenario) -> EnvioLoteResultado` para isto — hoje
`providers/orizon.py::enviar_lote` levanta `OperacaoNaoSuportada` de
propósito, esperando por `orizon_fature_client.py`/
`orizon_fature_xml_builder.py` (ainda não escritos, BACFF-014). Analisado o
manual do Fature linha a linha contra o contrato existente: ele expõe 3
operações de negócio — (a) enviar lote (`tissLoteGuias`), (b) consultar
status do protocolo (`tissSolicitacaoStatusProtocolo`), (c) demonstrativo de
retorno/glosa. (a) já cabe sem mudança de assinatura. Falta UMA função nova:

```
consultar_status_lote(lote, operator_config, mock_scenario='success')
    -> ConsultaStatusLoteResultado
```

Nova dataclass em `base.py`, mesmo espírito de `CancelamentoResultado`:
nunca levanta exceção de transporte, devolve `sucesso=False` com
`erro_code`/`erro_mensagem` técnicos em caso de falha. Carrega o status
agregado do lote (mapeado para `TISSLoteStatus`) e uma lista por guia
(`numero_guia_operadora`, `status_guia`, `codigo_glosa`, `descricao_glosa`)
— o demonstrativo de retorno/glosa (c) chega como efeito colateral desta
consulta, não como uma 6ª função separada.

**Por que estender o contrato existente em vez de criar um 2º port
paralelo:** um port de faturamento à parte duplicaria `ProviderError`/
`OperadoraDesativada`/`ProviderNaoRegistrado`/`ProviderNaoConfirmado`, o
registro em `_PROVIDERS`, a instrumentação (`_InstrumentedProvider`/
`OperatorCallLog`) e o teste de integridade parametrizado em
`tests_providers.py` — tudo por uma diferença real de 1 função. SOAP vs.
REST (para uma futura operadora direta) também não exige contrato
diferente: o contrato é assinatura Python pura, o provider decide o
transporte por baixo — nada aqui assume SOAP.

`capabilities().consulta_status` já existe como campo estático em
`ProviderCapabilities` desde a arquitetura original, mas sem função
correspondente no contrato (herdado do desenho do Autorize, que também não
implementou polling). Esta extensão fecha esse gap: `consulta_status=True`
passa a significar literalmente "este provider implementa
`consultar_status_lote`", consumido pela UI de TASK-BO-15 para decidir se
mostra "Atualizar status" manual.

**Fora do escopo do contrato agora:** Recurso de Glosa (Cap. 11 do manual —
contestação formal, operação de negócio distinta de consulta, não uma
leitura) e Cancela Guia/Lote do Fature (distinto do cancelamento do Autorize
já implementado em BACFF-014). Ambos ficam como função nova no contrato
quando forem implementados — não são adicionados especulativamente agora.

### 11.3 Impacto em TASK-BO-15 (relatórios de faturamento agnósticos de operadora)

O modelo de dados já existente (`TISSLote.operator_config`,
`TISSOperatorConfig.nome_operadora`/`registro_ans`) já é suficiente para
relatórios agregados por operadora real — **nenhuma migration nova é
necessária para TASK-BO-15.** A única mudança é de implementação (não de
schema): a query/serializer que alimenta "Prontas para Faturar" e o
histórico de lotes deve agrupar/filtrar por `operator_config.nome_operadora`
(ou `registro_ans`), NUNCA por `gateway_provider` — `gateway_provider` é
detalhe de transporte (pode ser `orizon` para 5 operadoras reais
diferentes); usá-lo como chave de relatório misturaria Bradesco + CarePlus +
Cabesp + Cassi + Seguros Unimed numa única linha "Orizon", exatamente o
anti-padrão que este documento existe para evitar.

### 11.4 Nível de generalização (anti-overengineering)

Esta rodada entrega SÓ o contrato (extensão de `base.py` com
`consultar_status_lote`/`ConsultaStatusLoteResultado`) e a implementação
real Orizon (`orizon_fature_client.py`/`orizon_fature_xml_builder.py`,
cobrindo as operadoras já credenciadas via Orizon: Bradesco/CarePlus).
Nenhum adapter especulativo para Sulamérica/Porto Seguro/Unimed
direto/NotreDame é escrito agora — cada um entra como um módulo novo em
`tiss/providers/` + uma entrada em `TISSGatewayProvider`/`_PROVIDERS` quando
(e se) existir contrato/credencial real, exatamente como a Orizon foi
adicionada.

### 11.5 Critérios de aceite formais (P0 — Orizon-Bradesco/CarePlus, contra o contrato genérico)

Ver `.claude/tasks/BACKOFFICE-TASKS-AVULSAS.md`, BACFF-017 (SyncroHealth,
tracker cross-repo) para os critérios Given/When/Then completos e o
detalhamento do plano de implementação. Resumo:

- Contrato: `ConsultaStatusLoteResultado` segue o mesmo padrão de
  `CancelamentoResultado` (nunca exceção de transporte, status por guia).
- `enviar_lote` da Orizon monta o envelope "WS" do Fature (distinto do
  Autorize e do genérico ANS), respeita o limite de 100 guias/lote (falha
  ANTES de qualquer I/O se excedido).
- `consultar_status_lote` devolve status por guia, glosa nunca aplicada ao
  lote inteiro quando só parte foi glosada.
- Operadora não atendida pela Orizon (`gateway_provider` diferente de
  `orizon`, ou `desconhecido`) nunca cai em fallback para o dialeto/endpoint
  Orizon — falha alto via `ProviderNaoConfirmado`/`ProviderNaoRegistrado`.
- Nenhum XML de request/response completo logado (extensão do teste
  anti-vazamento já existente em `tests_providers.py`).
- Suíte `tiss` e suíte completa verdes, sem regressão.

**Fora do critério de aceite desta rodada:** Recurso de Glosa, Cancela
Guia/Lote do Fature, particularidades adicionais por operadora (Cap. 8),
validação ponta a ponta contra homologação real, qualquer adapter para
operadora direta.
