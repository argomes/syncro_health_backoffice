"""
BO-08.3 — Client SOAP 1.1 para envio de documentos TISS (operação
tissEnvioDocumentos_Operation), contra o endpoint da operadora/hub
(ex.: Orizon — https://tiss-documentos.orizon.com.br/Service.asmx).

Regras da spec do produto (confirmadas contra o XSD oficial ANS
tissComplexTypesV4_02_00.xsd/ct_reciboDocumentos via projeto SoapUI —
correção de um parsing incorreto que buscava um elemento inexistente):
- Header `SOAPAction: ""` (vazio) — Orizon (e o padrão ANS) exige isso.
- Fault de transporte/SOAP: resposta contém <tissFaultWS> -> extrai
  codigoErro + descricaoErro (nível SOAP, requisição nem chegou a ser
  processada como negócio).
- Sucesso ou rejeição de NEGÓCIO (glosa) vêm ambos dentro de
  <reciboDocumentosWS><recebimentoDocumento>, que é uma CHOICE:
    - <reciboDocumentos><protocoloDoc> -> sucesso, extrai protocoloDoc
      (campo obrigatório no XSD; nrProtocoloRecebimento NÃO EXISTE no
      padrão TISS 4.02.00 — bug anterior buscava esse nome errado).
    - <mensagemErro><codigoGlosa>/<descricaoGlosa> -> operadora recebeu
      mas rejeitou por negócio (glosa), HTTP/SOAP ainda é "sucesso".
- Modo mock: TISS_SOAP_MOCK=true intercepta a chamada HTTP e devolve uma
  resposta fixa (mock_scenario='success'|'glosa'|'error') sem precisar de
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

# Estrutura confirmada contra o XSD oficial (ct_reciboDocumentos) e contra
# uma resposta gerada pelo projeto SoapUI a partir do WSDL real — cabecalho
# omitido deliberadamente (obrigatório no XSD real, mas o parser nunca o lê;
# incluir toda a árvore de origem/destino/identificacaoPrestador aqui só
# infla a fixture sem testar nada a mais).
MOCK_SUCCESS_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <reciboDocumentosWS xmlns="http://www.ans.gov.br/padroes/tiss/schemas">
      <recebimentoDocumento>
        <reciboDocumentos>
          <numeroDocumento>MOCK-DOC-000001</numeroDocumento>
          <protocoloDoc>MOCK-PROTO-000001</protocoloDoc>
        </reciboDocumentos>
      </recebimentoDocumento>
      <hash>mock-hash</hash>
    </reciboDocumentosWS>
  </soap:Body>
</soap:Envelope>"""

# Rejeição de NEGÓCIO (glosa) — operadora recebeu e processou a requisição
# (SOAP/HTTP é sucesso), mas rejeitou o documento. Estrutura tratada como
# SOAPFaultResult pelo parser (mesmo formato de campos codigo_erro/
# descricao_erro que o fault de transporte, para não duplicar o dataclass).
MOCK_GLOSA_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <reciboDocumentosWS xmlns="http://www.ans.gov.br/padroes/tiss/schemas">
      <recebimentoDocumento>
        <mensagemErro>
          <codigoGlosa>3105</codigoGlosa>
          <descricaoGlosa>Glosa mock configurada para teste</descricaoGlosa>
        </mensagemErro>
      </recebimentoDocumento>
      <hash>mock-hash</hash>
    </reciboDocumentosWS>
  </soap:Body>
</soap:Envelope>"""

# Fault de TRANSPORTE/SOAP — requisição nem chegou a ser processada como
# negócio (ex.: XML malformado, autenticação, etc). Nível diferente da
# glosa acima (que é MOCK_GLOSA_RESPONSE).
MOCK_ERROR_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <tissFaultWS xmlns="http://www.ans.gov.br/padroes/tiss/schemas">
      <codigoErro>599</codigoErro>
      <descricaoErro>Erro mock configurado para teste</descricaoErro>
    </tissFaultWS>
  </soap:Body>
</soap:Envelope>"""

# Elegibilidade — estrutura confirmada contra resposta real gerada pelo
# workspace SoapUI a partir de tissVerificaElegibilidadeV4_02_00.wsdl.
# respostaSolicitacao='S' -> beneficiário elegível, sem motivosNegativa.
MOCK_ELEGIBILIDADE_SUCCESS_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <respostaElegibilidadeWS xmlns="http://www.ans.gov.br/padroes/tiss/schemas">
      <respostaElegibilidade>
        <reciboElegibilidade>
          <registroANS>MOCK-ANS</registroANS>
          <numeroCarteira>MOCK-CARTEIRA-000001</numeroCarteira>
          <nomeBeneficiario>Beneficiario Mock</nomeBeneficiario>
          <respostaSolicitacao>S</respostaSolicitacao>
        </reciboElegibilidade>
      </respostaElegibilidade>
      <hash>mock-hash</hash>
    </respostaElegibilidadeWS>
  </soap:Body>
</soap:Envelope>"""

# respostaSolicitacao='N' -> beneficiário NÃO elegível, com motivosNegativa
# preenchido — consulta em si teve sucesso (não é SOAPFaultResult).
MOCK_ELEGIBILIDADE_NEGATIVA_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <respostaElegibilidadeWS xmlns="http://www.ans.gov.br/padroes/tiss/schemas">
      <respostaElegibilidade>
        <reciboElegibilidade>
          <registroANS>MOCK-ANS</registroANS>
          <numeroCarteira>MOCK-CARTEIRA-000002</numeroCarteira>
          <nomeBeneficiario>Beneficiario Mock Inelegivel</nomeBeneficiario>
          <respostaSolicitacao>N</respostaSolicitacao>
          <motivosNegativa>
            <motivoNegativa>
              <codigoGlosa>1822</codigoGlosa>
              <descricaoGlosa>Motivo mock configurado para teste</descricaoGlosa>
            </motivoNegativa>
          </motivosNegativa>
        </reciboElegibilidade>
      </respostaElegibilidade>
      <hash>mock-hash</hash>
    </respostaElegibilidadeWS>
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


@dataclass
class ElegibilidadeResult:
    """
    Resultado de tissVerificaElegibilidade_Operation — diferente de
    SOAPSuccessResult (envio de lote) porque o payload de negócio é outro:
    aqui "sucesso de transporte" não significa "beneficiário elegível", a
    própria resposta carrega elegivel=False com motivosNegativa preenchido
    quando o beneficiário não está apto (respostaSolicitacao='N' no XSD).
    """
    elegivel: bool
    numero_carteira: str
    nome_beneficiario: str
    motivos_negativa: list  # list[tuple[str, str]] de (codigo_glosa, descricao_glosa)
    raw_response: str


def _is_mock_enabled() -> bool:
    return getattr(settings, 'TISS_SOAP_MOCK', False)


def _build_envelope(xml_mensagem_tiss: str, operation: str = 'tissEnvioDocumentos_Operation') -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<soap:Envelope xmlns:soap="{SOAP_NAMESPACE}">'
        '<soap:Body>'
        f'<{operation} xmlns="http://www.ans.gov.br/padroes/tiss/schemas">'
        f'{xml_mensagem_tiss}'
        f'</{operation}>'
        '</soap:Body>'
        '</soap:Envelope>'
    )


def _parse_response(raw_response: str):
    """
    Retorna SOAPSuccessResult ou SOAPFaultResult a partir do XML de resposta
    (ignora namespace nas buscas, tolerante a variação de prefixo).

    BO-08.3 fix (achado ao montar o workspace SoapUI a partir do WSDL
    oficial): reciboDocumentosWS NÃO tem um nrProtocoloRecebimento direto —
    ele embrulha recebimentoDocumento, que é uma CHOICE entre
    reciboDocumentos (sucesso, campo protocoloDoc) e mensagemErro (glosa de
    negócio, codigoGlosa/descricaoGlosa). O fault de transporte (tissFaultWS)
    é um nível diferente, continua tratado separadamente.
    """
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

    recebimento = doc.find('.//{*}recebimentoDocumento')
    if recebimento is not None:
        recibo = recebimento.find('{*}reciboDocumentos')
        if recibo is not None:
            protocolo_el = recibo.find('{*}protocoloDoc')
            protocolo = protocolo_el.text if protocolo_el is not None else ''
            return SOAPSuccessResult(protocolo=protocolo or '', raw_response=raw_response)

        mensagem_erro = recebimento.find('{*}mensagemErro')
        if mensagem_erro is not None:
            codigo_el = mensagem_erro.find('{*}codigoGlosa')
            descricao_el = mensagem_erro.find('{*}descricaoGlosa')
            return SOAPFaultResult(
                codigo_erro=(codigo_el.text if codigo_el is not None else ''),
                descricao_erro=(descricao_el.text if descricao_el is not None else ''),
                raw_response=raw_response,
            )

    raise SOAPClientError('resposta_soap_sem_recibo_nem_fault')


def _parse_elegibilidade_response(raw_response: str):
    """
    Retorna ElegibilidadeResult ou SOAPFaultResult a partir de
    respostaElegibilidadeWS (confirmado contra resposta gerada pelo
    workspace SoapUI a partir de tissVerificaElegibilidadeV4_02_00.wsdl).

    respostaElegibilidade é uma CHOICE:
    - codigoGlosa/descricaoGlosa -> operadora rejeitou a PRÓPRIA consulta de
      elegibilidade (ex.: prestador não autorizado) — tratado como
      SOAPFaultResult, mesmo nível de "rejeição de negócio" da glosa de
      envio de lote.
    - reciboElegibilidade -> consulta processada; respostaSolicitacao
      ('S'/'N') indica se o BENEFICIÁRIO está elegível, não se a consulta
      teve sucesso — 'N' vem com motivosNegativa (lista de codigoGlosa/
      descricaoGlosa), mas ainda é um ElegibilidadeResult(elegivel=False),
      não um SOAPFaultResult (a consulta funcionou, a resposta é negativa).
    """
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

    resposta = doc.find('.//{*}respostaElegibilidade')
    if resposta is not None:
        recibo = resposta.find('{*}reciboElegibilidade')
        if recibo is not None:
            resposta_solic_el = recibo.find('{*}respostaSolicitacao')
            elegivel = (resposta_solic_el.text if resposta_solic_el is not None else '') == 'S'

            numero_carteira_el = recibo.find('{*}numeroCarteira')
            nome_beneficiario_el = recibo.find('{*}nomeBeneficiario')

            motivos_negativa = []
            for motivo in recibo.findall('{*}motivosNegativa/{*}motivoNegativa'):
                codigo_el = motivo.find('{*}codigoGlosa')
                descricao_el = motivo.find('{*}descricaoGlosa')
                motivos_negativa.append((
                    codigo_el.text if codigo_el is not None else '',
                    descricao_el.text if descricao_el is not None else '',
                ))

            return ElegibilidadeResult(
                elegivel=elegivel,
                numero_carteira=(numero_carteira_el.text if numero_carteira_el is not None else ''),
                nome_beneficiario=(nome_beneficiario_el.text if nome_beneficiario_el is not None else ''),
                motivos_negativa=motivos_negativa,
                raw_response=raw_response,
            )

        codigo_glosa_el = resposta.find('{*}codigoGlosa')
        if codigo_glosa_el is not None:
            codigo_el = codigo_glosa_el.find('{*}codigoGlosa')
            descricao_el = codigo_glosa_el.find('{*}descricaoGlosa')
            return SOAPFaultResult(
                codigo_erro=(codigo_el.text if codigo_el is not None else ''),
                descricao_erro=(descricao_el.text if descricao_el is not None else ''),
                raw_response=raw_response,
            )

    raise SOAPClientError('resposta_elegibilidade_sem_recibo_nem_fault')


def verificar_elegibilidade(endpoint_url: str, xml_mensagem_tiss: str, mock_scenario: str = 'success'):
    """
    Envia a consulta de elegibilidade (tissVerificaElegibilidade_Operation)
    via SOAP 1.1. Se TISS_SOAP_MOCK=true, intercepta e devolve uma resposta
    fixa (mock_scenario='success'|'negativa'|'error') sem request de rede.
    Retorna ElegibilidadeResult ou SOAPFaultResult.
    """
    if _is_mock_enabled():
        mock_responses = {
            'success': MOCK_ELEGIBILIDADE_SUCCESS_RESPONSE,
            'negativa': MOCK_ELEGIBILIDADE_NEGATIVA_RESPONSE,
        }
        raw = mock_responses.get(mock_scenario, MOCK_ERROR_RESPONSE)
        logger.info('soap_client: modo mock ativo (TISS_SOAP_MOCK=true), elegibilidade cenário=%s', mock_scenario)
        return _parse_elegibilidade_response(raw)

    envelope = _build_envelope(xml_mensagem_tiss, operation='tissVerificaElegibilidade_Operation')
    headers = {
        'Content-Type': 'text/xml; charset=utf-8',
        'SOAPAction': '""',
    }
    try:
        resp = httpx.post(endpoint_url, content=envelope.encode('utf-8'), headers=headers, timeout=DEFAULT_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.error('soap_client: falha de rede ao chamar endpoint de elegibilidade: %s', type(exc).__name__)
        raise SOAPClientError('soap_network_error') from exc

    return _parse_elegibilidade_response(resp.text)


def enviar_lote(endpoint_url: str, xml_mensagem_tiss: str, mock_scenario: str = 'success'):
    """
    Envia o XML montado via SOAP 1.1. Se TISS_SOAP_MOCK=true, intercepta e
    devolve uma resposta fixa (mock_scenario='success'|'glosa'|'error') sem
    request de rede. Retorna SOAPSuccessResult ou SOAPFaultResult.
    """
    if _is_mock_enabled():
        mock_responses = {
            'success': MOCK_SUCCESS_RESPONSE,
            'glosa': MOCK_GLOSA_RESPONSE,
        }
        raw = mock_responses.get(mock_scenario, MOCK_ERROR_RESPONSE)
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
