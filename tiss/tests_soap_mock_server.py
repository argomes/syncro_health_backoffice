import io
import uuid

from django.test import TestCase

from clinics.models import Clinic
from .models import TISSOperatorConfig, TISSLote, TISSGuia
from .xml_builder import build_lote_xml
from .soap_client import _build_envelope
from .management.commands.run_tiss_soap_mock import _wsgi_app


def _call_mock(body: bytes):
    """Invoca o app WSGI do mock diretamente, sem subir servidor real."""
    environ = {
        'REQUEST_METHOD': 'POST',
        'CONTENT_LENGTH': str(len(body)),
        'wsgi.input': io.BytesIO(body),
    }
    captured = {}

    def start_response(status, headers):
        captured['status'] = status
        captured['headers'] = headers

    result = b''.join(_wsgi_app(environ, start_response))
    return captured['status'], result


class TissSoapMockServerTests(TestCase):
    """
    O mock de CI faz o round-trip HTTP/XML de verdade (diferente de
    TISS_SOAP_MOCK=true, que intercepta in-process) — reaproveita
    xml_validator.validate_xml() contra o XSD oficial pra decidir se o
    mensagemTISS de um lote é aceito ou rejeitado.
    """

    def setUp(self):
        self.clinic = Clinic.objects.create(
            name='Clínica Mock Server', slug='mock-server-teste', cnpj='12.345.678/0001-99',
            db_name=f'db_{uuid.uuid4().hex[:8]}', db_user=f'u_{uuid.uuid4().hex[:8]}',
        )
        self.op = TISSOperatorConfig.objects.create(
            clinic=self.clinic, nome_operadora='Orizon', registro_ans='123456',
            endpoint_url='https://tiss-documentos.orizon.com.br/Service.asmx',
        )
        self.lote = TISSLote.objects.create(clinic=self.clinic, operator_config=self.op, numero_lote=1, competencia='2026-07')
        self.guia = TISSGuia.objects.create(
            clinic=self.clinic, numero='1', competencia='2026-07', numero_carteira='123', valor=150.5,
            procedimentos=[{'codigo': '10101012', 'descricao': 'Consulta', 'valor': 150.5, 'quantidade': 1}],
        )

    def test_mensagemTISS_valido_recebe_recibo_de_sucesso(self):
        xml_valido, _ = build_lote_xml(self.lote, [self.guia], self.clinic, self.op, '000000000001')
        # _build_envelope é a MESMA função que soap_client.enviar_lote usa de
        # verdade — inclui o fix de _strip_xml_declaration (achado ao rodar
        # este mesmo teste antes da correção: xml_completo com sua própria
        # declaração <?xml?> embutida crua gerava envelope malformado).
        envelope = _build_envelope(xml_valido).encode('utf-8')

        status, body = _call_mock(envelope)

        self.assertEqual(status, '200 OK')
        self.assertIn(b'reciboDocumentosWS', body)
        self.assertIn(b'protocoloDoc', body)

    def test_mensagemTISS_invalido_e_rejeitado_pelo_xsd_real(self):
        xml_invalido = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<mensagemTISS xmlns="http://www.ans.gov.br/padroes/tiss/schemas">'
            '<cabecalho></cabecalho>'
            '</mensagemTISS>'
        )
        envelope = _build_envelope(xml_invalido).encode('utf-8')

        status, body = _call_mock(envelope)

        # O mock retorna 200 (SOAP fault vem no corpo, não no status HTTP —
        # mesmo padrão que soap_client.py já assume ao não usar raise_for_status).
        self.assertEqual(status, '200 OK')
        self.assertIn(b'tissFaultWS', body)
        self.assertIn(b'mensagemTISS_invalido', body)

    def test_pedido_elegibilidade_recebe_resposta_canonica(self):
        envelope = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
            '<soap:Body><tissVerificaElegibilidade_Operation xmlns="http://www.ans.gov.br/padroes/tiss/schemas">'
            '<pedidoElegibilidade><numeroCarteira>123</numeroCarteira></pedidoElegibilidade>'
            '</tissVerificaElegibilidade_Operation></soap:Body></soap:Envelope>'
        ).encode('utf-8')

        status, body = _call_mock(envelope)

        self.assertEqual(status, '200 OK')
        self.assertIn(b'respostaElegibilidadeWS', body)

    def test_operacao_nao_reconhecida_retorna_fault(self):
        envelope = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
            '<soap:Body><algumaOutraOperacao/></soap:Body></soap:Envelope>'
        ).encode('utf-8')

        status, body = _call_mock(envelope)

        self.assertEqual(status, '200 OK')
        self.assertIn(b'operacao_nao_reconhecida', body)
