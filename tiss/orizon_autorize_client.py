"""
BACFF-014 — Client SOAP 1.1 para o Autorize da Orizon (autorização de
procedimento com elegibilidade embutida), DIFERENTE de soap_client.py
(que fala com o Fature/padrão genérico ANS de envio de lote).

Fonte de verdade: `Autorize-Integracao-Tecnica-Webservice-TISS-4-01-00.pdf`
(manual técnico OFICIAL da Orizon).

Endpoints de homologação confirmados no manual (Cap. 9):
- SOLICITACAO_PROCEDIMENTO:
  https://wsp.hom.orizonbrasil.com.br:6213/tiss/v40300/tissSolicitacaoProcedimento?wsdl
- SOLICITA_STATUS_AUTORIZACAO:
  https://wsp.hom.orizonbrasil.com.br:6213/tiss/v40300/tissSolicitacaoStatusAutorizacao?wsdl
- CANCELA_GUIA:
  https://wsp.hom.orizonbrasil.com.br:6213/tiss/v40300/tissCancelaGuia?wsdl
Produção: mesmos paths trocando "wsp.hom.orizonbrasil" por "wsp.orizonbrasil".

O manual não documenta explicitamente a estrutura XML de
`autorizacaoProcedimento` (resposta), só a mensagem de request e o fluxograma
(Cap. 10: solicitacaoProcedimento -> autorizacaoProcedimento; se "Em
Análise" -> solicitacaoStatusAutorizacao -> situacaoAutorizacao). O parser
abaixo segue o padrão TISS genérico para esse tipo de resposta (mesmo nome
de elementos usado no restante do padrão ANS: numeroGuiaOperadora,
situacaoAutorizacao/codigoGlosa quando negado) — precisa ser CONFIRMADO
contra uma resposta real de homologação assim que houver credenciais (ver
BACFF-014, bloqueado até clínica-piloto credenciada). Até lá, tratar como
melhor esforço documentado, não como fato confirmado.

Nunca loga XML completo (pode conter dados de beneficiário) — só protocolo/
status/código de erro.
"""
import logging
from dataclasses import dataclass
from enum import Enum

import httpx
from django.conf import settings
from lxml import etree

logger = logging.getLogger(__name__)

SOAP_NAMESPACE = 'http://schemas.xmlsoap.org/soap/envelope/'
DEFAULT_TIMEOUT = 30.0


class SituacaoAutorizacao(str, Enum):
    AUTORIZADO = 'autorizado'
    NEGADO = 'negado'
    EM_ANALISE = 'em_analise'


class OrizonAutorizeClientError(Exception):
    """Falha de rede/HTTP ao chamar o Autorize da Orizon."""


@dataclass
class AutorizacaoResult:
    situacao: SituacaoAutorizacao
    numero_guia_operadora: str
    codigo_glosa: str
    descricao_glosa: str
    raw_response: str


@dataclass
class SOAPFaultResult:
    codigo_erro: str
    descricao_erro: str
    raw_response: str


class SituacaoCancelamento(str, Enum):
    CANCELADO = 'cancelado'
    NAO_CANCELADO = 'nao_cancelado'


@dataclass
class CancelamentoResult:
    situacao: SituacaoCancelamento
    numero_guia_operadora: str
    raw_response: str


# ── Mocks (sem sandbox real ainda — ver BACFF-014) ──────────────────────────
# Estrutura de resposta ASSUMIDA por analogia com o padrão TISS genérico —
# não confirmada contra a Orizon real. Revisar quando houver credenciais.

MOCK_AUTORIZADO_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <sch:autorizacaoProcedimentoWS xmlns:sch="http://www.ans.gov.br/padroes/tiss/schemas">
      <sch:autorizacaoProcedimento>
        <sch:autorizacaoSP-SADT>
          <sch:cabecalhoSolicitacao>
            <sch:numeroGuiaOperadora>MOCK-GUIA-OP-000001</sch:numeroGuiaOperadora>
          </sch:cabecalhoSolicitacao>
          <sch:situacaoAutorizacao>1</sch:situacaoAutorizacao>
        </sch:autorizacaoSP-SADT>
      </sch:autorizacaoProcedimento>
      <sch:hash>mock-hash</sch:hash>
    </sch:autorizacaoProcedimentoWS>
  </soap:Body>
</soap:Envelope>"""

MOCK_NEGADO_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <sch:autorizacaoProcedimentoWS xmlns:sch="http://www.ans.gov.br/padroes/tiss/schemas">
      <sch:autorizacaoProcedimento>
        <sch:autorizacaoSP-SADT>
          <sch:situacaoAutorizacao>3</sch:situacaoAutorizacao>
          <sch:codigoGlosa>3144</sch:codigoGlosa>
          <sch:descricaoGlosa>Negativa mock configurada para teste</sch:descricaoGlosa>
        </sch:autorizacaoSP-SADT>
      </sch:autorizacaoProcedimento>
      <sch:hash>mock-hash</sch:hash>
    </sch:autorizacaoProcedimentoWS>
  </soap:Body>
</soap:Envelope>"""

MOCK_EM_ANALISE_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <sch:autorizacaoProcedimentoWS xmlns:sch="http://www.ans.gov.br/padroes/tiss/schemas">
      <sch:autorizacaoProcedimento>
        <sch:autorizacaoSP-SADT>
          <sch:situacaoAutorizacao>2</sch:situacaoAutorizacao>
        </sch:autorizacaoSP-SADT>
      </sch:autorizacaoProcedimento>
      <sch:hash>mock-hash</sch:hash>
    </sch:autorizacaoProcedimentoWS>
  </soap:Body>
</soap:Envelope>"""

MOCK_FAULT_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <sch:tissFaultWS xmlns:sch="http://www.ans.gov.br/padroes/tiss/schemas">
      <sch:codigoErro>LoginInvalido</sch:codigoErro>
      <sch:descricaoErro>Login ou senha inválidos (mock)</sch:descricaoErro>
    </sch:tissFaultWS>
  </soap:Body>
</soap:Envelope>"""

# ReciboCancelaGuia — o manual (Cap. 9, tabela de mensagens de resposta,
# citando só o NOME da mensagem) confirma que essa é a resposta da operação
# CANCELA_GUIA, mas — assim como acontece com autorizacaoProcedimento — NÃO
# documenta a estrutura XML exata da resposta, só do request (ver
# `_solicitacao_sp_sadt_xml`/manual, seção "b. XML Cancelamento de
# Autorização"). Estrutura abaixo assumida por analogia com o padrão
# situacaoAutorizacao/autorizacaoSP-SADT já usado no restante do módulo —
# precisa ser CONFIRMADA contra resposta real de homologação (mesmo
# bloqueio já registrado em BACFF-014 para `autorizacaoProcedimento`).
MOCK_CANCELADO_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <sch:reciboCancelaGuiaWS xmlns:sch="http://www.ans.gov.br/padroes/tiss/schemas">
      <sch:reciboCancelaGuia>
        <sch:numeroGuiaOperadora>MOCK-GUIA-OP-000001</sch:numeroGuiaOperadora>
        <sch:situacaoCancelamento>1</sch:situacaoCancelamento>
      </sch:reciboCancelaGuia>
      <sch:hash>mock-hash</sch:hash>
    </sch:reciboCancelaGuiaWS>
  </soap:Body>
</soap:Envelope>"""

MOCK_NAO_CANCELADO_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <sch:reciboCancelaGuiaWS xmlns:sch="http://www.ans.gov.br/padroes/tiss/schemas">
      <sch:reciboCancelaGuia>
        <sch:situacaoCancelamento>2</sch:situacaoCancelamento>
      </sch:reciboCancelaGuia>
      <sch:hash>mock-hash</sch:hash>
    </sch:reciboCancelaGuiaWS>
  </soap:Body>
</soap:Envelope>"""

_SITUACAO_MAP = {
    '1': SituacaoAutorizacao.AUTORIZADO,
    '2': SituacaoAutorizacao.EM_ANALISE,
    '3': SituacaoAutorizacao.NEGADO,
}

_SITUACAO_CANCELAMENTO_MAP = {
    '1': SituacaoCancelamento.CANCELADO,
    '2': SituacaoCancelamento.NAO_CANCELADO,
}


def _is_mock_enabled() -> bool:
    return getattr(settings, 'TISS_SOAP_MOCK', False)


def _parse_response(raw_response: str):
    doc = etree.fromstring(raw_response.encode('utf-8'))

    fault = doc.find('.//{*}tissFaultWS')
    if fault is not None:
        codigo_el = fault.find('.//{*}codigoErro')
        descricao_el = fault.find('.//{*}descricaoErro')
        return SOAPFaultResult(
            codigo_erro=(codigo_el.text if codigo_el is not None else ''),
            descricao_erro=(descricao_el.text if descricao_el is not None else ''),
            raw_response=raw_response,
        )

    autorizacao = doc.find('.//{*}autorizacaoSP-SADT')
    if autorizacao is not None:
        situacao_el = autorizacao.find('{*}situacaoAutorizacao')
        situacao_raw = situacao_el.text if situacao_el is not None else ''
        situacao = _SITUACAO_MAP.get(situacao_raw, SituacaoAutorizacao.EM_ANALISE)

        numero_guia_el = autorizacao.find('.//{*}numeroGuiaOperadora')
        codigo_glosa_el = autorizacao.find('{*}codigoGlosa')
        descricao_glosa_el = autorizacao.find('{*}descricaoGlosa')

        return AutorizacaoResult(
            situacao=situacao,
            numero_guia_operadora=(numero_guia_el.text if numero_guia_el is not None else ''),
            codigo_glosa=(codigo_glosa_el.text if codigo_glosa_el is not None else ''),
            descricao_glosa=(descricao_glosa_el.text if descricao_glosa_el is not None else ''),
            raw_response=raw_response,
        )

    raise OrizonAutorizeClientError('resposta_autorize_sem_autorizacao_nem_fault')


def _parse_cancelamento_response(raw_response: str):
    doc = etree.fromstring(raw_response.encode('utf-8'))

    fault = doc.find('.//{*}tissFaultWS')
    if fault is not None:
        codigo_el = fault.find('.//{*}codigoErro')
        descricao_el = fault.find('.//{*}descricaoErro')
        return SOAPFaultResult(
            codigo_erro=(codigo_el.text if codigo_el is not None else ''),
            descricao_erro=(descricao_el.text if descricao_el is not None else ''),
            raw_response=raw_response,
        )

    recibo = doc.find('.//{*}reciboCancelaGuia')
    if recibo is not None:
        situacao_el = recibo.find('{*}situacaoCancelamento')
        situacao_raw = situacao_el.text if situacao_el is not None else ''
        situacao = _SITUACAO_CANCELAMENTO_MAP.get(situacao_raw, SituacaoCancelamento.NAO_CANCELADO)

        numero_guia_el = recibo.find('{*}numeroGuiaOperadora')

        return CancelamentoResult(
            situacao=situacao,
            numero_guia_operadora=(numero_guia_el.text if numero_guia_el is not None else ''),
            raw_response=raw_response,
        )

    raise OrizonAutorizeClientError('resposta_cancela_guia_sem_recibo_nem_fault')


def cancelar_guia(endpoint_url: str, xml_cancelamento: str, mock_scenario: str = 'cancelado'):
    """
    Envia cancelaGuiaWS (Autorize Orizon) via SOAP 1.1 — mesmo padrão de
    `solicitar_autorizacao` (envelope, timeout, tratamento de erro de rede).
    Se TISS_SOAP_MOCK=true, intercepta e devolve resposta fixa
    (mock_scenario='cancelado'|'nao_cancelado'|'fault') sem rede real.
    Retorna CancelamentoResult ou SOAPFaultResult.

    Estrutura de resposta (ReciboCancelaGuia) NÃO confirmada contra a
    Orizon real — ver docstring de MOCK_CANCELADO_RESPONSE acima e
    BACFF-014 (mesmo bloqueio de homologação real já registrado para
    `solicitar_autorizacao`).
    """
    if _is_mock_enabled():
        mock_responses = {
            'cancelado': MOCK_CANCELADO_RESPONSE,
            'nao_cancelado': MOCK_NAO_CANCELADO_RESPONSE,
            'fault': MOCK_FAULT_RESPONSE,
        }
        raw = mock_responses.get(mock_scenario, MOCK_FAULT_RESPONSE)
        logger.info('orizon_autorize_client: modo mock ativo (TISS_SOAP_MOCK=true), cenário=%s', mock_scenario)
        return _parse_cancelamento_response(raw)

    headers = {
        'Content-Type': 'text/xml; charset=utf-8',
        'SOAPAction': '""',
    }
    try:
        resp = httpx.post(endpoint_url, content=xml_cancelamento.encode('utf-8'), headers=headers, timeout=DEFAULT_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.error('orizon_autorize_client: falha de rede ao chamar cancelaGuia: %s', type(exc).__name__)
        raise OrizonAutorizeClientError('soap_network_error') from exc

    return _parse_cancelamento_response(resp.text)


def solicitar_autorizacao(endpoint_url: str, xml_solicitacao: str, mock_scenario: str = 'autorizado'):
    """
    Envia solicitacaoProcedimentoWS (Autorize Orizon) via SOAP 1.1. Se
    TISS_SOAP_MOCK=true, intercepta e devolve resposta fixa
    (mock_scenario='autorizado'|'negado'|'em_analise'|'fault') sem rede real.
    Retorna AutorizacaoResult ou SOAPFaultResult.
    """
    if _is_mock_enabled():
        mock_responses = {
            'autorizado': MOCK_AUTORIZADO_RESPONSE,
            'negado': MOCK_NEGADO_RESPONSE,
            'em_analise': MOCK_EM_ANALISE_RESPONSE,
            'fault': MOCK_FAULT_RESPONSE,
        }
        raw = mock_responses.get(mock_scenario, MOCK_FAULT_RESPONSE)
        logger.info('orizon_autorize_client: modo mock ativo (TISS_SOAP_MOCK=true), cenário=%s', mock_scenario)
        return _parse_response(raw)

    headers = {
        'Content-Type': 'text/xml; charset=utf-8',
        'SOAPAction': '""',
    }
    try:
        resp = httpx.post(endpoint_url, content=xml_solicitacao.encode('utf-8'), headers=headers, timeout=DEFAULT_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.error('orizon_autorize_client: falha de rede ao chamar Autorize: %s', type(exc).__name__)
        raise OrizonAutorizeClientError('soap_network_error') from exc

    return _parse_response(resp.text)


def consultar_status_autorizacao(endpoint_url: str, xml_consulta: str, mock_scenario: str = 'em_analise'):
    """
    BO-08.5 — Envia `solicitacaoStatusAutorizacaoWS` (Autorize Orizon) via
    SOAP 1.1, consultando o status de uma autorização anteriormente
    registrada como "Em Análise" por `solicitar_autorizacao`. Mesmo padrão
    de transporte (envelope pronto vindo do builder, timeout, tratamento de
    erro de rede).

    A resposta (`ans:situacaoAutorizacaoWS`, confirmado contra
    `tissSolicitacaoStatusAutorizacaoV4_02_00.wsdl`) reaproveita a MESMA
    estrutura de `autorizacaoSP-SADT` (`situacaoAutorizacao`/
    `numeroGuiaOperadora`/`codigoGlosa`/`descricaoGlosa`) já tratada por
    `_parse_response` — a busca ali é `.//{*}autorizacaoSP-SADT` (qualquer
    profundidade, independente do wrapper que embrulha), então nenhuma
    duplicação de parser é necessária para o wrapper diferente.

    Se TISS_SOAP_MOCK=true, intercepta e devolve resposta fixa
    (mock_scenario='autorizado'|'negado'|'em_analise'|'fault') — reaproveita
    os MESMOS mocks de `solicitar_autorizacao` (estrutura de resposta
    idêntica, só o nome do wrapper raiz mudaria, e o parser ignora o nome do
    wrapper). Retorna AutorizacaoResult ou SOAPFaultResult.
    """
    if _is_mock_enabled():
        mock_responses = {
            'autorizado': MOCK_AUTORIZADO_RESPONSE,
            'negado': MOCK_NEGADO_RESPONSE,
            'em_analise': MOCK_EM_ANALISE_RESPONSE,
            'fault': MOCK_FAULT_RESPONSE,
        }
        raw = mock_responses.get(mock_scenario, MOCK_FAULT_RESPONSE)
        logger.info(
            'orizon_autorize_client: modo mock ativo (TISS_SOAP_MOCK=true), consulta de status cenário=%s',
            mock_scenario,
        )
        return _parse_response(raw)

    headers = {
        'Content-Type': 'text/xml; charset=utf-8',
        'SOAPAction': '""',
    }
    try:
        resp = httpx.post(endpoint_url, content=xml_consulta.encode('utf-8'), headers=headers, timeout=DEFAULT_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.error('orizon_autorize_client: falha de rede ao consultar status de autorização: %s', type(exc).__name__)
        raise OrizonAutorizeClientError('soap_network_error') from exc

    return _parse_response(resp.text)
