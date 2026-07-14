"""
BO-08.3 — Client SOAP 1.1 para envio de documentos TISS (operação
tissEnvioDocumentos_Operation), contra o endpoint da operadora/hub
(ex.: Orizon — https://tiss-documentos.orizon.com.br/Service.asmx).

Regras da spec do produto:
- Header `SOAPAction: ""` (vazio) — Orizon (e o padrão ANS) exige isso.
- Sucesso: resposta contém <reciboDocumentosWS> -> extrai nrProtocoloRecebimento.
- Erro: resposta contém <tissFaultWS> -> extrai codigoErro + descricaoErro.
- Modo mock: TISS_SOAP_MOCK=true intercepta a chamada HTTP e devolve uma
  resposta fixa (sucesso ou erro, conforme `mock_scenario`) sem precisar de
  credenciais/rede reais — permite testar BO-08.1..BO-08.4 de ponta a ponta.

Nunca loga o XML enviado/recebido inteiro (pode conter dados de
beneficiário) — só protocolo/código de erro e metadados não sensíveis.
"""
import logging
from dataclasses import dataclass

import httpx
from django.conf import settings
from lxml import etree

logger = logging.getLogger(__name__)

SOAP_NAMESPACE = 'http://schemas.xmlsoap.org/soap/envelope/'
DEFAULT_TIMEOUT = 30.0

MOCK_SUCCESS_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <reciboDocumentosWS xmlns="http://www.ans.gov.br/padroes/tiss/schemas">
      <nrProtocoloRecebimento>MOCK-PROTO-000001</nrProtocoloRecebimento>
    </reciboDocumentosWS>
  </soap:Body>
</soap:Envelope>"""

MOCK_ERROR_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <tissFaultWS xmlns="http://www.ans.gov.br/padroes/tiss/schemas">
      <codigoErro>599</codigoErro>
      <descricaoErro>Erro mock configurado para teste</descricaoErro>
    </tissFaultWS>
  </soap:Body>
</soap:Envelope>"""


class SOAPClientError(Exception):
    """Falha de rede/HTTP ao chamar o endpoint SOAP da operadora."""


@dataclass
class SOAPSuccessResult:
    protocolo: str
    raw_response: str


@dataclass
class SOAPFaultResult:
    codigo_erro: str
    descricao_erro: str
    raw_response: str


def _is_mock_enabled() -> bool:
    return getattr(settings, 'TISS_SOAP_MOCK', False)


def _build_envelope(xml_mensagem_tiss: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<soap:Envelope xmlns:soap="{SOAP_NAMESPACE}">'
        '<soap:Body>'
        '<tissEnvioDocumentos_Operation xmlns="http://www.ans.gov.br/padroes/tiss/schemas">'
        f'{xml_mensagem_tiss}'
        '</tissEnvioDocumentos_Operation>'
        '</soap:Body>'
        '</soap:Envelope>'
    )


def _parse_response(raw_response: str):
    """
    Retorna SOAPSuccessResult ou SOAPFaultResult a partir do XML de resposta
    (ignora namespace nas buscas, tolerante a variação de prefixo).
    """
    doc = etree.fromstring(raw_response.encode('utf-8'))

    recibo = doc.find('.//{*}reciboDocumentosWS')
    if recibo is not None:
        protocolo_el = recibo.find('.//{*}nrProtocoloRecebimento')
        protocolo = protocolo_el.text if protocolo_el is not None else ''
        return SOAPSuccessResult(protocolo=protocolo or '', raw_response=raw_response)

    fault = doc.find('.//{*}tissFaultWS')
    if fault is not None:
        codigo_el = fault.find('.//{*}codigoErro')
        descricao_el = fault.find('.//{*}descricaoErro')
        return SOAPFaultResult(
            codigo_erro=(codigo_el.text if codigo_el is not None else ''),
            descricao_erro=(descricao_el.text if descricao_el is not None else ''),
            raw_response=raw_response,
        )

    raise SOAPClientError('resposta_soap_sem_recibo_nem_fault')


def enviar_lote(endpoint_url: str, xml_mensagem_tiss: str, mock_scenario: str = 'success'):
    """
    Envia o XML montado via SOAP 1.1. Se TISS_SOAP_MOCK=true, intercepta e
    devolve uma resposta fixa (mock_scenario='success' ou 'error') sem
    request de rede. Retorna SOAPSuccessResult ou SOAPFaultResult.
    """
    if _is_mock_enabled():
        raw = MOCK_SUCCESS_RESPONSE if mock_scenario == 'success' else MOCK_ERROR_RESPONSE
        logger.info('soap_client: modo mock ativo (TISS_SOAP_MOCK=true), cenário=%s', mock_scenario)
        return _parse_response(raw)

    envelope = _build_envelope(xml_mensagem_tiss)
    headers = {
        'Content-Type': 'text/xml; charset=utf-8',
        'SOAPAction': '""',
    }
    try:
        resp = httpx.post(endpoint_url, content=envelope.encode('utf-8'), headers=headers, timeout=DEFAULT_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.error('soap_client: falha de rede ao chamar endpoint da operadora: %s', type(exc).__name__)
        raise SOAPClientError('soap_network_error') from exc

    # SOAP fault pode vir com status HTTP 500 mesmo sendo uma resposta de
    # negócio válida (tissFaultWS) — não usamos raise_for_status aqui, o
    # parser decide sucesso/erro pelo conteúdo, não pelo status HTTP.
    return _parse_response(resp.text)
