"""
BACFF-014 — Client SOAP 1.1 para o Autorize da Orizon (autorização de
procedimento com elegibilidade embutida), DIFERENTE de soap_client.py
(que fala com o Fature/padrão genérico ANS de envio de lote).

Fonte de verdade: `Autorize-Integracao-Tecnica-Webservice-TISS-4-01-00.pdf`
(manual técnico OFICIAL da Orizon).

Endpoints de homologação confirmados no manual (Cap. 9):
- SOLICITACAO_PROCEDIMENTO:
  https://wsp.hom.orizonbrasil.com.br:6213/tiss/v40100/tissSolicitacaoProcedimento?wsdl
- SOLICITA_STATUS_AUTORIZACAO:
  https://wsp.hom.orizonbrasil.com.br:6213/tiss/v40100/tissSolicitacaoStatusAutorizacao?wsdl
- CANCELA_GUIA:
  https://wsp.hom.orizonbrasil.com.br:6213/tiss/v40100/tissCancelaGuia?wsdl
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

_SITUACAO_MAP = {
    '1': SituacaoAutorizacao.AUTORIZADO,
    '2': SituacaoAutorizacao.EM_ANALISE,
    '3': SituacaoAutorizacao.NEGADO,
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
