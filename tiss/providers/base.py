"""
Contrato do provider de operadora TISS — o "port de saída" do módulo TISS.

Fonte de verdade do desenho: `.claude/tasks/TISS-OPERATOR-PROVIDER-ARCHITECTURE.md`
(§4). Este módulo contém SÓ o contrato (docstrings + dataclasses de retorno +
exceções). A resolução/registro vive em `tiss/providers/__init__.py`; cada
implementação vive em `tiss/providers/<provider>.py`.

--------------------------------------------------------------------------
O CONTRATO (4 funções, nada além disso no MVP)
--------------------------------------------------------------------------

Todo módulo registrado em `_PROVIDERS` DEVE expor exatamente estas quatro
funções de módulo (não classes — não há estado por provider):

1) verificar_cobertura(clinic, operator_config, numero_carteira,
                       beneficiario_nome='', appointment_id='',
                       mock_scenario='success') -> ElegibilidadeRespostaCompleta

   **D1 (decisão do Tech Lead, 2026-07-28): elegibilidade e autorização são
   UM conceito só neste contrato.** O documento de arquitetura originalmente
   previa `verificar_elegibilidade` e `solicitar_autorizacao` como duas
   funções, com `capabilities().elegibilidade_isolada` distinguindo quem
   suporta qual. Isso foi descartado: a pergunta de negócio é uma só —
   "esse paciente está coberto para este atendimento?" — e cada provider
   resolve internamente para a(s) operação(ões) SOAP que a operadora dele
   expõe. A Orizon não tem elegibilidade isolada (a cobertura vem embutida
   em `solicitacaoProcedimentoWS`); uma operadora direta provavelmente terá
   `tissVerificaElegibilidade`. Nos dois casos o chamador chama a MESMA
   função e recebe o MESMO tipo normalizado — é exatamente essa
   indiferença que evita `if operadora == 'orizon'` vazar para a view, para
   o gateway Go e para a UI da recepção.

   NUNCA levanta exceção de rede/transporte para o chamador: falha de
   transporte vira um `ElegibilidadeRespostaCompleta` com `elegivel=False`,
   `status_operacional=FALHA_TRANSPORTE` e `erro_mensagem` técnica. A
   recepcionista precisa de uma resposta, não de um 500.

2) enviar_lote(lote, guias, sequencial_transacao, mock_scenario='success')
       -> EnvioLoteResultado

   O provider é dono de montar o XML no dialeto/versão da operadora,
   validá-lo e transportá-lo. Ele NÃO persiste nada: quem escreve
   `TISSLote.status`/`protocolo`/`erro_mensagem` e cria `TISSGlosa` é
   `tiss/services.py::enviar_lote`, a partir do `EnvioLoteResultado`
   devolvido aqui. Também não levanta exceção de transporte — devolve
   `sucesso=False` com `erro_code` preenchido.

3) health_check(operator_config) -> ProviderHealth

   Sonda ATIVA, barata e explicitamente sob demanda (botão "Testar conexão"
   no admin da config). **Nunca** dispara uma autorização/consulta real:
   isso tem custo contratual e usaria dado de paciente. Para endpoints SOAP
   o padrão é um GET no `?wsdl`. Não levanta exceção — devolve
   `ProviderHealth(reachable=False, ...)`.

4) capabilities() -> ProviderCapabilities

   Estática por provider (não depende de config). Consumida pelo serializer
   de `TISSOperatorConfig` para que gateway e UI habilitem botões por
   CAPACIDADE, nunca por nome de operadora.

--------------------------------------------------------------------------
REGRAS NÃO NEGOCIÁVEIS PARA QUALQUER PROVIDER NOVO (LGPD)
--------------------------------------------------------------------------
- Credencial só via Fernet (`operator_config.login_plain`/`senha_plain`),
  nunca em texto plano no banco, em `__str__`, em `list_display`, em
  serializer ou em log.
- Nenhum provider pode logar XML de request/response completo (contém nome
  e carteirinha do beneficiário). Só protocolo, código de erro e metadado
  não sensível. Há teste anti-vazamento parametrizado sobre `_PROVIDERS`
  em `tests_providers.py` — ele é bloqueante de merge.
- `erro_mensagem` em qualquer dataclass daqui é sempre TÉCNICA. Quem monta
  a string é responsável por essa garantia.

--------------------------------------------------------------------------
PONTOS DE EXTENSÃO DELIBERADAMENTE NÃO IMPLEMENTADOS
--------------------------------------------------------------------------
- **Certificado digital / mTLS (EDGW-041, D5 NÃO fechado):** a topologia do
  certificado (Backoffice vs. gateway da clínica) ainda não foi decidida
  pelo Tech Lead. O contrato acima não assume nada sobre isso: o provider é
  dono da autenticação, então quando a decisão sair, o provider que precisar
  de mTLS configura seu próprio transporte sem mudar nenhuma assinatura
  daqui. Nenhum campo de certificado foi adicionado a `TISSOperatorConfig`.
- **Orizon Fature (envio de lote), D4:** fora de escopo desta rodada por
  decisão de ordem. `providers/orizon.py::enviar_lote` devolve
  `OperacaoNaoSuportada` explicitamente até `orizon_fature_client.py`
  existir — nunca faz fallback para o dialeto genérico.
"""
from dataclasses import dataclass, field

from ..models import TISSElegibilidadeStatus


# ---------------------------------------------------------------------------
# Exceções do contrato
# ---------------------------------------------------------------------------

class ProviderError(Exception):
    """
    Base de todas as falhas ESTRUTURAIS de resolução/capacidade de provider
    (diferente de falha de rede/negócio, que vira dataclass de resultado).

    `code` é a string estável consumida por `tiss/views.py` para escolher o
    HTTP status e devolvida ao gateway Go em `{"error": <code>}`. Mudar um
    code é mudança de contrato de API — não fazer sem alinhar com o gateway.
    """
    code = 'provider_error'

    def __init__(self, message: str = ''):
        super().__init__(message or self.code)


class OperadoraDesativada(ProviderError):
    """
    `TISSOperatorConfig.ativo is False` — o "desplugar" de Nível 1 (§4.2 do
    documento): contrato encerrado, credencial revogada, operadora fora do ar.

    Mapeada para **409 Conflict** (deliberadamente NÃO 404): o gateway
    precisa distinguir "operadora não cadastrada" de "cadastrada e
    desligada" para mostrar a mensagem certa na recepção.

    D2 (decisão do Tech Lead, 2026-07-28): isto bloqueia SÓ o caminho
    automático/SOAP. `services.registrar_elegibilidade_manual` continua
    funcionando normalmente com a operadora desativada — a clínica degrada
    para o fluxo manual, que é fallback de primeira classe, em vez de parar.
    """
    code = 'operadora_desativada'


class ProviderNaoRegistrado(ProviderError):
    """
    "Desplugar" de Nível 2 (§4.2): a config aponta para um valor de
    `gateway_provider` que não existe em `_PROVIDERS`. Deve falhar ALTO —
    nunca fazer fallback silencioso para outro provider. Mandar payload no
    dialeto errado para uma operadora real gera glosa e retrabalho de
    faturamento na clínica, um custo bem pior que um erro explícito.
    """
    code = 'provider_nao_registrado'


class ProviderNaoConfirmado(ProviderError):
    """
    D3 (decisão do Tech Lead, 2026-07-28): a config está no provider
    `desconhecido` — nenhuma compatibilidade de dialeto foi confirmada com
    a operadora real. Antes desta arquitetura, `generico_ans` era o DEFAULT
    SILENCIOSO do model: qualquer config criada sem escolha explícita
    tentava falar o dialeto genérico ANS, que nenhuma operadora nossa
    confirmou aceitar (e que sequer envia credencial — buraco B4 do
    documento). Agora isso falha claramente, antes de qualquer I/O.
    """
    code = 'provider_nao_confirmado'


class OperacaoNaoSuportada(ProviderError):
    """
    O provider existe e está ativo, mas não implementa ESTA operação de
    negócio (ex.: Orizon + envio de lote, que depende do Fature — D4).
    Distinto de `ProviderNaoConfirmado`: aqui o provider é legítimo, só é
    parcial.
    """
    code = 'operacao_nao_suportada'


# ---------------------------------------------------------------------------
# Dataclasses de retorno (os tipos normalizados do "port")
# ---------------------------------------------------------------------------

@dataclass
class ElegibilidadeRespostaCompleta:
    """
    BACFF-AVULSA-01: o resultado COMPLETO (conteúdo clínico) da consulta —
    devolvido na resposta HTTP síncrona ao Edge Gateway, mas nunca
    persistido no banco central. Quem chama (views.py) serializa isto
    diretamente, sem passar pelo model/DB. A cópia duradoura fica no banco
    LOCAL da clínica (Edge Gateway), responsabilidade de quem consome esta
    resposta — não deste serviço.

    Vivia em `tiss/services.py` até a arquitetura de providers; foi movida
    para cá porque é o tipo de retorno do contrato (services agora depende
    de providers, não o contrário). `services` reexporta o nome para não
    quebrar quem já importava de lá.

    `status_operacional` NÃO é serializado para o gateway — é metadado
    técnico que diz a `services` qual `TISSElegibilidadeStatus` registrar no
    log operacional. Ficou aqui (em vez de o provider chamar o logger
    direto) para manter os providers livres de qualquer escrita em banco:
    persistência é responsabilidade exclusiva de `services`.
    """
    elegivel: bool
    numero_carteira: str
    beneficiario_nome: str
    origem: str
    motivos_negativa: list = field(default_factory=list)
    numero_guia_operadora: str = ''
    erro_mensagem: str = ''
    status_operacional: str = TISSElegibilidadeStatus.SUCESSO


@dataclass
class EnvioLoteResultado:
    """
    Retorno normalizado de `provider.enviar_lote`. O provider preenche;
    `services.enviar_lote` traduz para transições de `TISSLote.status`,
    criação de `TISSGlosa` e `TISSServiceError`.

    `erro_code` usa os mesmos códigos que `services` já expunha antes desta
    refatoração (`xml_builder_failed`, `xsd_validator_unavailable`,
    `xml_schema_invalid`, `soap_send_failed`, `soap_fault`) — o contrato de
    erro da API REST não muda.

    `xml_enviado`/`raw_response` viajam neste dataclass porque `services`
    precisa persisti-los em `TISSLote` (campos de negócio, já existentes e
    já cobertos pelas regras de acesso do admin). Não devem ser logados.
    """
    sucesso: bool
    protocolo: str = ''
    xml_enviado: str = ''
    hash_epilogo: str = ''
    raw_response: str = ''
    erro_code: str = ''
    erro_mensagem: str = ''
    codigo_glosa: str = ''
    descricao_glosa: str = ''


@dataclass
class ProviderHealth:
    """Retorno de `health_check` — §4.4(b). Sonda ativa, sob demanda."""
    reachable: bool
    latency_ms: int = 0
    detail: str = ''


@dataclass
class DocumentoAssinaturaFragmento:
    """
    TASK-BO-10 — retorno normalizado de `provider.preparar_documento_assinatura`:
    o fragmento C14N (sem `<Signature>`) que a fila de assinatura XMLDSig
    (`tiss/xmldsig_service.py`) grava em `TISSDocumentoAssinatura.fragmento_canonico`
    e que o gateway da clínica deve assinar. `root_tag` é usado só para achar
    o ponto de inserção textual do bloco de assinatura depois — nunca para
    reparsear o fragmento.
    """
    fragmento_canonico: str
    root_tag: str


@dataclass
class EnvioDocumentoAssinadoResultado:
    """
    TASK-BO-10 — retorno normalizado de `provider.enviar_documento_assinado`
    (transmissão do XML já assinado, `TISSDocumentoAssinatura.xml_final`).
    Mesmo espírito de `EnvioLoteResultado`: o provider NUNCA levanta exceção
    de transporte, devolve `sucesso=False` com `erro_code` preenchido.
    """
    sucesso: bool
    protocolo: str = ''
    raw_response: str = ''
    erro_code: str = ''
    erro_mensagem: str = ''


@dataclass(frozen=True)
class ProviderCapabilities:
    """
    §4.5 — o que ESTE provider sabe fazer. Estático por provider.

    D1: **não existe `elegibilidade_isolada`.** A distinção entre
    "elegibilidade" e "autorização" foi removida do contrato por decisão do
    Tech Lead; `cobertura` é a capacidade unificada. Se um dia a UI precisar
    saber que uma operadora demora (Orizon "Em Análise"), a informação certa
    é `consulta_status` (existe polling?), não "que tipo de operação SOAP
    roda por baixo" — que é justamente o detalhe que o provider esconde.

    `confirmado_em_homologacao` é o que separa "temos código" de "temos
    integração validada contra a operadora real" (Fase 2 do checklist de
    onboarding, §5 do documento). Fica False até haver evidência arquivada.
    """
    cobertura: bool = False
    envio_lote: bool = False
    consulta_status: bool = False
    cancelamento_guia: bool = False
    # TASK-BO-10: envioDocumentoWS assinado (XMLDSig) — só a Orizon confirma
    # hoje. Distinto de `envio_lote` (loteGuiasWS/Fature, sem assinatura).
    envio_documento_assinado: bool = False
    versoes_padrao_suportadas: tuple = ()
    exige_credenciais: bool = False
    confirmado_em_homologacao: bool = False

    def as_dict(self) -> dict:
        return {
            'cobertura': self.cobertura,
            'envio_lote': self.envio_lote,
            'consulta_status': self.consulta_status,
            'cancelamento_guia': self.cancelamento_guia,
            'envio_documento_assinado': self.envio_documento_assinado,
            'versoes_padrao_suportadas': list(self.versoes_padrao_suportadas),
            'exige_credenciais': self.exige_credenciais,
            'confirmado_em_homologacao': self.confirmado_em_homologacao,
        }
