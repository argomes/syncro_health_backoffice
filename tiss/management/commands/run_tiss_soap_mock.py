"""
Mock HTTP/SOAP do endpoint da operadora (Orizon), pra rodar em CI/E2E como um
processo real na rede — diferente de TISS_SOAP_MOCK=true (que intercepta
dentro do próprio processo Python, nunca serializa/desserializa XML de
verdade). Este mock:

- Recebe o POST SOAP de verdade (envelope + operation wrapper).
- Reaproveita tiss.xml_validator.validate_xml() (o MESMO validador contra o
  XSD oficial usado antes do envio real) pra decidir se o mensagemTISS de um
  lote é estruturalmente válido — rejeita com tissFaultWS se não for, em vez
  de aceitar qualquer coisa.
- Reaproveita os fixtures de resposta já corrigidos contra o schema real em
  tiss.soap_client (MOCK_SUCCESS_RESPONSE, MOCK_GLOSA_RESPONSE,
  MOCK_ELEGIBILIDADE_SUCCESS_RESPONSE) — não duplica a estrutura de resposta
  uma terceira vez.

Uso (CI ou local):
    python manage.py run_tiss_soap_mock --port 9999
Depois, apontar TISSOperatorConfig.endpoint_url (ambiente de teste) para
http://localhost:9999/ e desligar TISS_SOAP_MOCK (o cliente vai fazer o
POST HTTP de verdade contra este mock, exercendo o código de rede real de
soap_client.py — TISS_SOAP_MOCK continua existindo pra testes unitários
in-process, este mock é para os cenários que precisam do round-trip HTTP).

Limitação atual: só a validação de lote (mensagemTISS) reaproveita XSD real
via xml_validator.py. A requisição de elegibilidade (pedidoElegibilidadeWS)
ainda não tem um validador estrutural equivalente — aceita qualquer
requisição reconhecível como consulta de elegibilidade e devolve a resposta
canônica de sucesso. Estender quando a camada de persistência/endpoint de
elegibilidade for construída.
"""
from wsgiref.simple_server import make_server

from django.core.management.base import BaseCommand
from lxml import etree

from tiss.xml_validator import validate_xml, XMLValidatorError
from tiss.soap_client import (
    MOCK_SUCCESS_RESPONSE,
    MOCK_ELEGIBILIDADE_SUCCESS_RESPONSE,
)

_FAULT_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <tissFaultWS xmlns="http://www.ans.gov.br/padroes/tiss/schemas">
      <codigoErro>{codigo}</codigoErro>
      <descricaoErro>{descricao}</descricaoErro>
    </tissFaultWS>
  </soap:Body>
</soap:Envelope>"""


def _fault(codigo: str, descricao: str) -> bytes:
    # Nunca ecoa o XML recebido na resposta de erro (pode conter dado de
    # beneficiário) — só a mensagem de validação já sanitizada pelo
    # xml_validator (linha/coluna/mensagem estrutural, não conteúdo).
    return _FAULT_TEMPLATE.format(codigo=codigo, descricao=descricao).encode('utf-8')


def _handle_request(body: bytes) -> bytes:
    try:
        doc = etree.fromstring(body)
    except etree.XMLSyntaxError as exc:
        return _fault('400', f'xml_malformado: {exc}')

    mensagem_tiss = doc.find('.//{*}mensagemTISS')
    if mensagem_tiss is not None:
        xml_str = etree.tostring(mensagem_tiss, encoding='unicode')
        try:
            issues = validate_xml(xml_str)
        except XMLValidatorError as exc:
            return _fault('500', f'mock_sem_xsd_configurado: {exc}')

        if issues:
            return _fault('422', f'mensagemTISS_invalido: {issues[0]}')

        return MOCK_SUCCESS_RESPONSE.encode('utf-8')

    pedido_elegibilidade = doc.find('.//{*}pedidoElegibilidade')
    if pedido_elegibilidade is not None:
        # Sem validador XSD equivalente para ct_elegibilidadeVerifica ainda
        # (ver limitação no docstring do módulo) — aceita e devolve sucesso.
        return MOCK_ELEGIBILIDADE_SUCCESS_RESPONSE.encode('utf-8')

    return _fault('400', 'operacao_nao_reconhecida')


def _wsgi_app(environ, start_response):
    if environ.get('REQUEST_METHOD') != 'POST':
        start_response('405 Method Not Allowed', [('Content-Type', 'text/plain')])
        return [b'apenas POST']

    length = int(environ.get('CONTENT_LENGTH') or 0)
    body = environ['wsgi.input'].read(length)

    response_body = _handle_request(body)
    start_response('200 OK', [
        ('Content-Type', 'text/xml; charset=utf-8'),
        ('Content-Length', str(len(response_body))),
    ])
    return [response_body]


class Command(BaseCommand):
    help = 'Sobe um mock HTTP do endpoint SOAP da operadora TISS para testes E2E/CI (round-trip HTTP real, não in-process).'

    def add_arguments(self, parser):
        parser.add_argument('--port', type=int, default=9999)
        parser.add_argument('--host', type=str, default='127.0.0.1')

    def handle(self, *args, **options):
        host, port = options['host'], options['port']
        server = make_server(host, port, _wsgi_app)
        self.stdout.write(self.style.SUCCESS(f'Mock SOAP TISS rodando em http://{host}:{port}/'))
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            self.stdout.write('Encerrado.')
