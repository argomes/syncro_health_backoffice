from django.test import TestCase, override_settings

from .orizon_autorize_client import (
    solicitar_autorizacao, AutorizacaoResult, SOAPFaultResult, SituacaoAutorizacao,
)


@override_settings(TISS_SOAP_MOCK=True)
class OrizonAutorizeClientMockTests(TestCase):
    def test_cenario_autorizado_extrai_numero_guia_operadora(self):
        resultado = solicitar_autorizacao('https://fake-endpoint', '<sch:solicitacaoProcedimentoWS/>', mock_scenario='autorizado')
        self.assertIsInstance(resultado, AutorizacaoResult)
        self.assertEqual(resultado.situacao, SituacaoAutorizacao.AUTORIZADO)
        self.assertEqual(resultado.numero_guia_operadora, 'MOCK-GUIA-OP-000001')

    def test_cenario_negado_extrai_codigo_e_descricao_glosa(self):
        resultado = solicitar_autorizacao('https://fake-endpoint', '<sch:solicitacaoProcedimentoWS/>', mock_scenario='negado')
        self.assertIsInstance(resultado, AutorizacaoResult)
        self.assertEqual(resultado.situacao, SituacaoAutorizacao.NEGADO)
        self.assertEqual(resultado.codigo_glosa, '3144')
        self.assertTrue(resultado.descricao_glosa)

    def test_cenario_em_analise(self):
        resultado = solicitar_autorizacao('https://fake-endpoint', '<sch:solicitacaoProcedimentoWS/>', mock_scenario='em_analise')
        self.assertIsInstance(resultado, AutorizacaoResult)
        self.assertEqual(resultado.situacao, SituacaoAutorizacao.EM_ANALISE)

    def test_cenario_fault_extrai_codigo_e_descricao_erro(self):
        """
        Fault de transporte (ex: LoginInvalido) é um nível diferente de
        'negado' — 'negado' é a operadora processando e recusando por
        negócio (ex.: procedimento não coberto); fault é a requisição nem
        chegando a ser processada (ex.: credenciais erradas).
        """
        resultado = solicitar_autorizacao('https://fake-endpoint', '<sch:solicitacaoProcedimentoWS/>', mock_scenario='fault')
        self.assertIsInstance(resultado, SOAPFaultResult)
        self.assertEqual(resultado.codigo_erro, 'LoginInvalido')
        self.assertTrue(resultado.descricao_erro)

    def test_mock_nao_faz_chamada_de_rede(self):
        # endpoint claramente inatingível — se o mock tentasse rede de verdade,
        # levantaria OrizonAutorizeClientError; como não levanta, confirma
        # que a interceptação acontece antes de qualquer I/O.
        resultado = solicitar_autorizacao('https://endpoint-inexistente.invalid', '<sch:solicitacaoProcedimentoWS/>', mock_scenario='autorizado')
        self.assertIsInstance(resultado, AutorizacaoResult)

    def test_cenario_desconhecido_cai_no_fault(self):
        resultado = solicitar_autorizacao('https://fake-endpoint', '<sch:solicitacaoProcedimentoWS/>', mock_scenario='cenario-nao-existe')
        self.assertIsInstance(resultado, SOAPFaultResult)
