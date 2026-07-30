import uuid
from django.test import TestCase, override_settings

from clinics.models import Clinic
from .models import TISSOperatorConfig, TISSGuia
from .orizon_autorize_xml_builder import (
    build_solicitacao_procedimento_xml, build_cancelamento_guia_xml, OrizonAutorizeXMLBuilderError,
    get_tiss_padrao_versao_orizon,
)


def _make_clinic(slug):
    return Clinic.objects.create(
        name='Clínica Teste',
        slug=slug,
        cnpj='12.345.678/0001-99',
        db_name=f'db_{uuid.uuid4().hex[:8]}',
        db_user=f'u_{uuid.uuid4().hex[:8]}',
    )


class OrizonAutorizeXMLBuilderTests(TestCase):
    def setUp(self):
        self.clinic = _make_clinic('orizon-autorize-xml-teste')
        self.op = TISSOperatorConfig.objects.create(
            clinic=self.clinic, nome_operadora='Orizon', registro_ans='123456',
            endpoint_url='https://wsp.hom.orizonbrasil.com.br:6213/tiss/v40100/tissSolicitacaoProcedimento',
        )
        self.op.set_login('teste001')
        self.op.set_senha('senha-teste')
        self.op.save()
        self.guia = TISSGuia.objects.create(
            clinic=self.clinic, numero='1', competencia='2026-07', numero_carteira='999',
            valor=150.5,
            procedimentos=[{'codigo': '10101012', 'descricao': 'Consulta', 'valor': 150.5, 'quantidade': 1}],
        )

    def test_gera_xml_bem_formado_com_estrutura_do_autorize(self):
        xml, hash_md5 = build_solicitacao_procedimento_xml(self.guia, self.clinic, self.op, '000000000001')
        # Wrapper "WS" direto, sem mensagemTISS/epilogo (diferente do padrão ANS genérico)
        self.assertIn('<sch:solicitacaoProcedimentoWS', xml)
        self.assertIn('<sch:cabecalho>', xml)
        self.assertIn('<sch:solicitacaoProcedimento>', xml)
        self.assertIn('<sch:solicitacaoSP-SADT>', xml)
        self.assertNotIn('mensagemTISS', xml)
        self.assertNotIn('<epilogo>', xml)
        self.assertIn(f'<sch:hash>{hash_md5}</sch:hash>', xml)
        self.assertTrue(xml.startswith('<?xml version="1.0" encoding="UTF-8"?>'))

    def test_versao_do_padrao_default_e_4_03_00_nao_hardcoded_em_4_01_00(self):
        # BACFF-014 (achado 1, atualização 2026-07-29): versão não pode mais
        # ficar hardcoded em 4.01.00 — default passa a ser 4.03.00, vindo de
        # settings.TISS_PADRAO_VERSAO_ORIZON, não de constante fixa no módulo.
        xml, _ = build_solicitacao_procedimento_xml(self.guia, self.clinic, self.op, '1')
        self.assertIn('<sch:Padrao>4.03.00</sch:Padrao>', xml)
        self.assertNotIn('<sch:Padrao>4.01.00</sch:Padrao>', xml)

    @override_settings(TISS_PADRAO_VERSAO_ORIZON='4.02.00')
    def test_versao_do_padrao_e_parametrizavel_via_settings(self):
        # Confere que a versão vem de settings (não de constante fixa) —
        # trocando o setting, o XML muda de acordo.
        self.assertEqual(get_tiss_padrao_versao_orizon(), '4.02.00')
        xml, _ = build_solicitacao_procedimento_xml(self.guia, self.clinic, self.op, '1')
        self.assertIn('<sch:Padrao>4.02.00</sch:Padrao>', xml)

    def test_indicacao_clinica_presente_no_xml(self):
        # BACFF-014 (achado 2, 2026-07-29): elemento obrigatório pelo manual
        # 4.03.00 (Cap. 10), filho de solicitacaoSP-SADT.
        self.guia.indicacao_clinica = 'J06.9 - Infecção aguda das vias aéreas superiores'
        self.guia.save()
        xml, _ = build_solicitacao_procedimento_xml(self.guia, self.clinic, self.op, '1')
        self.assertIn(
            '<sch:indicacaoClinica>J06.9 - Infecção aguda das vias aéreas superiores</sch:indicacaoClinica>',
            xml,
        )

    def test_indicacao_clinica_tem_placeholder_quando_ausente(self):
        # Elemento é obrigatório pelo schema — nunca deve sair vazio, mesmo
        # sem CID/indicação registrada na guia.
        xml, _ = build_solicitacao_procedimento_xml(self.guia, self.clinic, self.op, '1')
        self.assertIn('<sch:indicacaoClinica>', xml)
        self.assertNotIn('<sch:indicacaoClinica></sch:indicacaoClinica>', xml)

    def test_login_senha_prestador_presente_com_senha_em_md5(self):
        import hashlib
        xml, _ = build_solicitacao_procedimento_xml(self.guia, self.clinic, self.op, '1')
        self.assertIn('<sch:loginPrestador>teste001</sch:loginPrestador>', xml)
        senha_hash_esperado = hashlib.md5('senha-teste'.encode('utf-8')).hexdigest()
        self.assertIn(f'<sch:senhaPrestador>{senha_hash_esperado}</sch:senhaPrestador>', xml)
        self.assertNotIn('senha-teste', xml)  # senha em texto plano nunca deve aparecer

    def test_hash_nao_inclui_a_si_mesmo(self):
        xml, hash_md5 = build_solicitacao_procedimento_xml(self.guia, self.clinic, self.op, '1')
        # Hash é calculado sobre o corpo SEM a declaração <?xml ...?> (só
        # prependida depois, ao montar xml_completo) — mesma regra de
        # xml_builder.py/build_lote_xml, replicada aqui por consistência.
        declaracao = '<?xml version="1.0" encoding="UTF-8"?>'
        corpo_sem_hash = xml[len(declaracao):].split('<sch:hash>')[0]
        import hashlib
        self.assertEqual(hashlib.md5(corpo_sem_hash.encode('utf-8')).hexdigest(), hash_md5)

    def test_operadora_sem_login_configurado_levanta_erro(self):
        op_sem_login = TISSOperatorConfig.objects.create(
            clinic=self.clinic, nome_operadora='Cassi', registro_ans='654321',
            endpoint_url='https://example.com',
        )
        with self.assertRaises(OrizonAutorizeXMLBuilderError):
            build_solicitacao_procedimento_xml(self.guia, self.clinic, op_sem_login, '1')

    def test_guia_none_levanta_erro(self):
        with self.assertRaises(OrizonAutorizeXMLBuilderError):
            build_solicitacao_procedimento_xml(None, self.clinic, self.op, '1')

    def test_numero_carteira_e_registro_ans_presentes(self):
        xml, _ = build_solicitacao_procedimento_xml(self.guia, self.clinic, self.op, '1')
        self.assertIn('<sch:numeroCarteira>999</sch:numeroCarteira>', xml)
        self.assertIn('<sch:registroANS>123456</sch:registroANS>', xml)

    def test_caracteres_especiais_sao_escapados(self):
        guia_acento = TISSGuia.objects.create(
            clinic=self.clinic, numero='2', competencia='2026-07', numero_carteira='ção&<>',
            valor=100,
            procedimentos=[{'codigo': '1', 'descricao': 'Exame & Consulta <especial>', 'valor': 100, 'quantidade': 1}],
        )
        xml, _ = build_solicitacao_procedimento_xml(guia_acento, self.clinic, self.op, '1')
        self.assertNotIn('ção&<>', xml)
        self.assertIn('&amp;', xml)

    # BACFF-014 (P1, 2026-07-30): campos ausentes confirmados contra o manual
    # (Cap. 10, mesmo exemplo já usado para indicacaoClinica/ausenciaCodValidacao).
    def test_campos_p1_presentes_no_xml(self):
        xml, _ = build_solicitacao_procedimento_xml(self.guia, self.clinic, self.op, '1')
        self.assertIn('<sch:numeroGuiaPrincipal>', xml)
        self.assertIn('<sch:codValidacao>', xml)
        self.assertIn('<sch:nomeProfissional>', xml)
        self.assertIn('<sch:observacao>', xml)

    def test_numero_guia_principal_usa_atributo_da_guia_quando_disponivel(self):
        self.guia.numero_guia_principal = '9999'
        xml, _ = build_solicitacao_procedimento_xml(self.guia, self.clinic, self.op, '1')
        self.assertIn('<sch:numeroGuiaPrincipal>9999</sch:numeroGuiaPrincipal>', xml)

    def test_nome_profissional_usa_atributo_da_guia_quando_disponivel(self):
        self.guia.nome_profissional_solicitante = 'Dra. Fulana de Tal'
        xml, _ = build_solicitacao_procedimento_xml(self.guia, self.clinic, self.op, '1')
        self.assertIn('<sch:nomeProfissional>Dra. Fulana de Tal</sch:nomeProfissional>', xml)

    def test_observacao_usa_atributo_da_guia_quando_disponivel(self):
        self.guia.observacao = 'Paciente com alergia a dipirona'
        xml, _ = build_solicitacao_procedimento_xml(self.guia, self.clinic, self.op, '1')
        self.assertIn('<sch:observacao>Paciente com alergia a dipirona</sch:observacao>', xml)


class OrizonCancelamentoGuiaXMLBuilderTests(TestCase):
    """
    BACFF-014 (2026-07-30) — schema confirmado contra o manual Autorize
    4.03.00, seção "b. XML Cancelamento de Autorização" (página 30-31 do
    PDF `20260515_Autorize Integração Tecnica Webservice - TISS 4.03.00.pdf`).
    """

    def setUp(self):
        self.clinic = _make_clinic('orizon-cancelamento-xml-teste')
        self.op = TISSOperatorConfig.objects.create(
            clinic=self.clinic, nome_operadora='Orizon', registro_ans='123456',
            endpoint_url='https://wsp.hom.orizonbrasil.com.br:6213/tiss/v40300/tissCancelaGuia',
        )
        self.op.set_login('teste001')
        self.op.set_senha('senha-teste')
        self.op.save()
        self.guia = TISSGuia.objects.create(
            clinic=self.clinic, numero='1234', competencia='2026-07', numero_carteira='999',
        )

    def test_gera_xml_bem_formado_com_estrutura_de_cancelaguia(self):
        xml, hash_md5 = build_cancelamento_guia_xml(self.guia, self.clinic, self.op, '2')
        self.assertIn('<ans:cancelaGuiaWS', xml)
        self.assertIn('<ans:cabecalho>', xml)
        self.assertIn('<ans:tipoTransacao>CANCELA_GUIA</ans:tipoTransacao>', xml)
        self.assertIn('<ans:cancelaGuia>', xml)
        self.assertIn('<ans:tipoCancelamento>', xml)
        self.assertIn('<ans:tipoCancelamentoGuia>', xml)
        self.assertIn('<ans:tipoGuia>1</ans:tipoGuia>', xml)
        self.assertIn('<ans:numeroGuiaPrestador>1234</ans:numeroGuiaPrestador>', xml)
        self.assertNotIn('mensagemTISS', xml)
        self.assertNotIn('<epilogo>', xml)
        self.assertIn(f'<ans:hash>{hash_md5}</ans:hash>', xml)
        self.assertTrue(xml.startswith('<?xml version="1.0" encoding="UTF-8"?>'))

    def test_cabecalho_cancelamento_nao_inclui_login_senha(self):
        """
        Confirmado contra o exemplo literal do manual: o cabeçalho de
        CANCELA_GUIA não tem <loginSenhaPrestador> (diferente do cabeçalho
        de SOLICITACAO_PROCEDIMENTOS) — reproduzido fielmente, não presumido.
        """
        xml, _ = build_cancelamento_guia_xml(self.guia, self.clinic, self.op, '2')
        self.assertNotIn('loginSenhaPrestador', xml)

    def test_padrao_versao_presente(self):
        xml, _ = build_cancelamento_guia_xml(self.guia, self.clinic, self.op, '2')
        self.assertIn(f'<ans:Padrao>{get_tiss_padrao_versao_orizon()}</ans:Padrao>', xml)

    def test_guia_none_levanta_erro(self):
        with self.assertRaises(OrizonAutorizeXMLBuilderError):
            build_cancelamento_guia_xml(None, self.clinic, self.op, '2')

    def test_hash_nao_inclui_a_si_mesmo(self):
        import hashlib
        xml, hash_md5 = build_cancelamento_guia_xml(self.guia, self.clinic, self.op, '2')
        declaracao = '<?xml version="1.0" encoding="UTF-8"?>'
        corpo_sem_hash = xml[len(declaracao):].split('<ans:hash>')[0]
        self.assertEqual(hashlib.md5(corpo_sem_hash.encode('utf-8')).hexdigest(), hash_md5)
