import uuid
from django.test import TestCase

from clinics.models import Clinic
from .models import TISSOperatorConfig, TISSLote, TISSGuia
from .xml_builder import build_lote_xml, XMLBuilderError


def _make_clinic(slug):
    return Clinic.objects.create(
        name='Clínica Teste',
        slug=slug,
        cnpj='12.345.678/0001-99',
        db_name=f'db_{uuid.uuid4().hex[:8]}',
        db_user=f'u_{uuid.uuid4().hex[:8]}',
    )


class XMLBuilderTests(TestCase):
    def setUp(self):
        self.clinic = _make_clinic('xml-builder-teste')
        self.op = TISSOperatorConfig.objects.create(
            clinic=self.clinic, nome_operadora='Orizon', registro_ans='123456',
            endpoint_url='https://tiss-documentos.orizon.com.br/Service.asmx',
        )
        self.lote = TISSLote.objects.create(clinic=self.clinic, operator_config=self.op, numero_lote=1, competencia='2026-07')
        self.guia = TISSGuia.objects.create(
            clinic=self.clinic, numero='1', competencia='2026-07', numero_carteira='123',
            valor=150.5,
            procedimentos=[{'codigo': '10101012', 'descricao': 'Consulta acentuação', 'valor': 150.5, 'quantidade': 1}],
        )

    def test_gera_xml_bem_formado_com_estrutura_esperada(self):
        xml, hash_md5 = build_lote_xml(self.lote, [self.guia], self.clinic, self.op, '000000000001')
        self.assertIn('<mensagemTISS', xml)
        self.assertIn('<cabecalho>', xml)
        self.assertIn('<prestadorParaOperadora>', xml)
        self.assertIn('<loteGuias>', xml)
        self.assertIn('<epilogo><hash>', xml)
        self.assertIn(hash_md5, xml)
        self.assertTrue(xml.startswith('<?xml version="1.0" encoding="UTF-8"?>'))

    def test_hash_nao_inclui_epilogo_nem_signature(self):
        xml, hash_md5 = build_lote_xml(self.lote, [self.guia], self.clinic, self.op, '000000000001')
        corpo_sem_epilogo = xml.split('<epilogo>')[0]
        import hashlib
        self.assertEqual(hashlib.md5(corpo_sem_epilogo.encode('utf-8')).hexdigest(), hash_md5)

    def test_caracteres_especiais_sao_escapados(self):
        guia_acento = TISSGuia.objects.create(
            clinic=self.clinic, numero='2', competencia='2026-07', numero_carteira='456',
            valor=99.9,
            procedimentos=[{'codigo': '10101013', 'descricao': 'Consulta & avaliação <especial>', 'valor': 99.9, 'quantidade': 1}],
        )
        xml, _ = build_lote_xml(self.lote, [guia_acento], self.clinic, self.op, '000000000002')
        self.assertIn('&amp;', xml)
        self.assertIn('&lt;especial&gt;', xml)
        # Não deve conter a tag literal não escapada dentro do texto
        self.assertNotIn('avaliação <especial>', xml)

    def test_lote_sem_guias_levanta_erro(self):
        with self.assertRaises(XMLBuilderError):
            build_lote_xml(self.lote, [], self.clinic, self.op, '000000000003')

    def test_multiplas_guias_no_mesmo_lote(self):
        guia2 = TISSGuia.objects.create(
            clinic=self.clinic, numero='3', competencia='2026-07', numero_carteira='789', valor=50,
            procedimentos=[{'codigo': '10101014', 'descricao': 'Retorno', 'valor': 50, 'quantidade': 1}],
        )
        xml, _ = build_lote_xml(self.lote, [self.guia, guia2], self.clinic, self.op, '000000000004')
        self.assertEqual(xml.count('<guiaSP-SADT>'), 2)
