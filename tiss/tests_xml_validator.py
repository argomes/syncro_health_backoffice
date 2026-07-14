import uuid
from django.test import TestCase, override_settings

from clinics.models import Clinic
from .models import TISSOperatorConfig, TISSLote, TISSGuia
from .xml_builder import build_lote_xml
from .xml_validator import validate_xml, XMLValidatorError, reset_schema_cache


class XMLValidatorTests(TestCase):
    def setUp(self):
        self.clinic = Clinic.objects.create(
            name='Clínica Validador', slug='xml-validator-teste', cnpj='12.345.678/0001-99',
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

    def test_xml_valido_contra_xsd_oficial(self):
        xml, _ = build_lote_xml(self.lote, [self.guia], self.clinic, self.op, '000000000001')
        issues = validate_xml(xml)
        self.assertEqual(issues, [])

    def test_xml_malformado_gera_erro_localizado(self):
        xml_quebrado = '<mensagemTISS><cabecalho></mensagemTISS>'  # tag não fechada corretamente
        issues = validate_xml(xml_quebrado)
        self.assertTrue(len(issues) >= 1)
        self.assertTrue(issues[0].line >= 1)
        self.assertTrue(issues[0].message)

    def test_xml_sem_campos_obrigatorios_rejeitado_pelo_xsd(self):
        xml_incompleto = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<mensagemTISS xmlns="http://www.ans.gov.br/padroes/tiss/schemas">'
            '<cabecalho></cabecalho>'
            '<epilogo><hash>x</hash></epilogo>'
            '</mensagemTISS>'
        )
        issues = validate_xml(xml_incompleto)
        self.assertTrue(len(issues) >= 1)
        for issue in issues:
            self.assertTrue(issue.line >= 0)

    @override_settings(TISS_XSD_DIR='/caminho/que/nao/existe')
    def test_xsd_dir_invalido_levanta_erro_de_configuracao(self):
        reset_schema_cache()
        with self.assertRaises(XMLValidatorError):
            validate_xml('<mensagemTISS/>')
        reset_schema_cache()  # restaura cache para os próximos testes da suíte
