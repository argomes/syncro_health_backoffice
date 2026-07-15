from django.test import TestCase, override_settings

from .soap_client import enviar_lote, SOAPSuccessResult, SOAPFaultResult


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

    def test_mock_nao_faz_chamada_de_rede(self):
        # endpoint claramente inválido/inatingível — se o mock tentasse rede,
        # isso levantaria SOAPClientError; como não levanta, confirma que a
        # interceptação ocorreu antes de qualquer httpx.post real.
        resultado = enviar_lote('http://endereco-que-nao-existe.invalid', '<mensagemTISS/>', mock_scenario='success')
        self.assertIsInstance(resultado, SOAPSuccessResult)
