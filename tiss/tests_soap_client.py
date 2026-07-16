from django.test import TestCase, override_settings

from .soap_client import (
    enviar_lote, verificar_elegibilidade,
    SOAPSuccessResult, SOAPFaultResult, ElegibilidadeResult,
)


@override_settings(TISS_SOAP_MOCK=True)
class SOAPClientMockTests(TestCase):
    def test_cenario_sucesso_extrai_protocolo(self):
        resultado = enviar_lote('https://fake-endpoint', '<mensagemTISS/>', mock_scenario='success')
        self.assertIsInstance(resultado, SOAPSuccessResult)
        self.assertEqual(resultado.protocolo, 'MOCK-PROTO-000001')

    def test_cenario_erro_extrai_codigo_e_descricao(self):
        resultado = enviar_lote('https://fake-endpoint', '<mensagemTISS/>', mock_scenario='error')
        self.assertIsInstance(resultado, SOAPFaultResult)
        self.assertEqual(resultado.codigo_erro, '599')
        self.assertTrue(resultado.descricao_erro)

    def test_cenario_glosa_extrai_codigo_e_descricao(self):
        """
        BO-08.3 fix: glosa de negócio vem embrulhada dentro de
        reciboDocumentosWS/recebimentoDocumento/mensagemErro (SOAP/HTTP é
        sucesso, mas a operadora rejeitou o documento) — nível diferente do
        fault de transporte (tissFaultWS, cenário 'error' acima). Confirma
        que o parser distingue as duas choices de recebimentoDocumento
        (reciboDocumentos vs mensagemErro) corretamente.
        """
        resultado = enviar_lote('https://fake-endpoint', '<mensagemTISS/>', mock_scenario='glosa')
        self.assertIsInstance(resultado, SOAPFaultResult)
        self.assertEqual(resultado.codigo_erro, '3105')
        self.assertTrue(resultado.descricao_erro)

    def test_mock_nao_faz_chamada_de_rede(self):
        # endpoint claramente inválido/inatingível — se o mock tentasse rede,
        # isso levantaria SOAPClientError; como não levanta, confirma que a
        # interceptação ocorreu antes de qualquer httpx.post real.
        resultado = enviar_lote('http://endereco-que-nao-existe.invalid', '<mensagemTISS/>', mock_scenario='success')
        self.assertIsInstance(resultado, SOAPSuccessResult)


@override_settings(TISS_SOAP_MOCK=True)
class VerificarElegibilidadeMockTests(TestCase):
    """
    tissVerificaElegibilidade_Operation — estrutura de respostaElegibilidadeWS
    confirmada contra resposta real gerada pelo workspace SoapUI a partir de
    tissVerificaElegibilidadeV4_02_00.wsdl (WSDL oficial ANS, em
    /Users/andersonrodriguesgomes/projetos/mvp/Documents/PadroTISSComunicao202505/
    Padrão TISS Comunicação 040200/).
    """

    def test_cenario_success_beneficiario_elegivel(self):
        resultado = verificar_elegibilidade('https://fake-endpoint', '<pedidoElegibilidade/>', mock_scenario='success')
        self.assertIsInstance(resultado, ElegibilidadeResult)
        self.assertTrue(resultado.elegivel)
        self.assertEqual(resultado.numero_carteira, 'MOCK-CARTEIRA-000001')
        self.assertEqual(resultado.motivos_negativa, [])

    def test_cenario_negativa_beneficiario_inelegivel_com_motivos(self):
        """
        respostaSolicitacao='N' não é um erro de transporte/SOAP — a
        consulta funcionou, a resposta é que o beneficiário não está apto.
        Continua sendo ElegibilidadeResult, não SOAPFaultResult.
        """
        resultado = verificar_elegibilidade('https://fake-endpoint', '<pedidoElegibilidade/>', mock_scenario='negativa')
        self.assertIsInstance(resultado, ElegibilidadeResult)
        self.assertFalse(resultado.elegivel)
        self.assertEqual(resultado.motivos_negativa, [('1822', 'Motivo mock configurado para teste')])

    def test_cenario_erro_de_transporte_continua_soap_fault(self):
        resultado = verificar_elegibilidade('https://fake-endpoint', '<pedidoElegibilidade/>', mock_scenario='error')
        self.assertIsInstance(resultado, SOAPFaultResult)
        self.assertEqual(resultado.codigo_erro, '599')

    def test_mock_nao_faz_chamada_de_rede(self):
        resultado = verificar_elegibilidade(
            'http://endereco-que-nao-existe.invalid', '<pedidoElegibilidade/>', mock_scenario='success',
        )
        self.assertIsInstance(resultado, ElegibilidadeResult)
