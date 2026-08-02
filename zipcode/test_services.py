from unittest.mock import patch, MagicMock

from django.db import IntegrityError
from django.test import TestCase

from zipcode.models import Cep
from zipcode.services import CepService, normalizar_cep


class NormalizarCepTests(TestCase):
    def test_remove_hifen_e_espacos(self):
        self.assertEqual(normalizar_cep("01310-930"), "01310930")
        self.assertEqual(normalizar_cep(" 01310 930 "), "01310930")

    def test_string_vazia_ou_none_retorna_vazio(self):
        self.assertEqual(normalizar_cep(""), "")
        self.assertEqual(normalizar_cep(None), "")


class CepServiceCacheAsideTestCase(TestCase):
    """Testa a regra de negócio do Cache-Aside (Lazy Loading) do Service"""

    def setUp(self):
        self.cep_cacheado = Cep.objects.create(
            codigo_ibge="3550308",
            cep="01310930",
            logradouro="Avenida Paulista",
            bairro="Bela Vista",
            localidade="São Paulo",
            uf="SP",
        )

    @patch('zipcode.services.ApiCepProvider')
    def test_cache_hit_retorna_direto_do_banco_sem_chamar_api(self, mock_provider_cls):
        resultado = CepService.buscar_cep("01310930")

        mock_provider_cls.assert_not_called()
        self.assertEqual(resultado["logradouro"], "Avenida Paulista")
        self.assertEqual(resultado["cep"], "01310930")

    @patch('zipcode.services.ApiCepProvider')
    def test_cache_hit_com_cep_formatado_diferente_ainda_bate_no_cache(self, mock_provider_cls):
        """
        Regressão do bug de normalização: o cliente manda "01310-930"
        (com hífen) mas o registro foi salvo sem hífen — antes da
        correção isso ia parar (incorretamente) na API externa.
        """
        resultado = CepService.buscar_cep("01310-930")

        mock_provider_cls.assert_not_called()
        self.assertEqual(resultado["cep"], "01310930")

    @patch('zipcode.services.ApiCepProvider')
    def test_cache_miss_chama_api_e_salva_no_banco(self, mock_provider_cls):
        mock_provider = MagicMock()
        mock_provider.find_cep.return_value = {
            "cep": "04538-132",
            "logradouro": "Avenida Brigadeiro Faria Lima",
            "bairro": "Itaim Bibi",
            "localidade": "São Paulo",
            "uf": "SP",
            "ibge": "3550308",
            "gia": "1004",
            "ddd": "11",
            "siafi": "7107",
        }
        mock_provider_cls.return_value = mock_provider

        resultado = CepService.buscar_cep("04538132")

        mock_provider.find_cep.assert_called_once_with("04538132")
        self.assertEqual(resultado["logradouro"], "Avenida Brigadeiro Faria Lima")
        self.assertTrue(Cep.objects.filter(cep="04538132").exists())
        # Mesmo codigo_ibge do CEP já cacheado em setUp — não pode
        # violar unicidade (um município tem N ceps).
        self.assertEqual(
            Cep.objects.filter(codigo_ibge="3550308").count(), 2
        )

    @patch('zipcode.services.ApiCepProvider')
    def test_cache_miss_com_api_externa_falhando_retorna_dict_vazio_sem_quebrar(self, mock_provider_cls):
        mock_provider = MagicMock()
        mock_provider.find_cep.return_value = {}
        mock_provider_cls.return_value = mock_provider

        resultado = CepService.buscar_cep("99999999")

        self.assertEqual(resultado, {})
        self.assertFalse(Cep.objects.filter(cep="99999999").exists())

    def test_cep_vazio_ou_none_retorna_dict_vazio(self):
        self.assertEqual(CepService.buscar_cep(""), {})
        self.assertEqual(CepService.buscar_cep(None), {})

    @patch('zipcode.services.ApiCepProvider')
    def test_corrida_de_cache_miss_concorrente_e_idempotente(self, mock_provider_cls):
        """
        Duas requisições concorrentes para o mesmo CEP novo: a segunda
        gravação bate em IntegrityError (unique=cep) — o service deve
        engolir isso e apenas reler o registro já persistido pela
        primeira, em vez de propagar erro 500 pro gateway.
        """
        mock_provider = MagicMock()
        mock_provider.find_cep.return_value = {
            "cep": "04538-132",
            "logradouro": "Avenida Brigadeiro Faria Lima",
            "bairro": "Itaim Bibi",
            "localidade": "São Paulo",
            "uf": "SP",
            "ibge": "3550308",
        }
        mock_provider_cls.return_value = mock_provider

        # Simula que outra requisição já gravou o registro entre o
        # DoesNotExist e o save() desta chamada.
        Cep.objects.create(
            codigo_ibge="3550308",
            cep="04538132",
            logradouro="Avenida Brigadeiro Faria Lima",
            uf="SP",
        )

        with patch('zipcode.models.Cep.save', side_effect=IntegrityError("dup")):
            resultado = CepService.buscar_cep("04538132")

        self.assertEqual(resultado["cep"], "04538132")
        self.assertEqual(Cep.objects.filter(cep="04538132").count(), 1)

    def test_serialize_nao_referencia_campo_inexistente_nome(self):
        """
        Regressão: `_serialize` referenciava `cep.nome`, campo que não
        existe no modelo `Cep` (era `localidade`) — quebrava toda
        chamada (cache hit e miss) com AttributeError.
        """
        resultado = CepService._serialize(self.cep_cacheado)
        self.assertNotIn('nome', resultado)
        self.assertEqual(resultado['localidade'], "São Paulo")


class CepServiceBuscarLogradouroTestCase(TestCase):
    def setUp(self):
        Cep.objects.create(
            codigo_ibge="3550308",
            cep="01310930",
            logradouro="Avenida Paulista",
            localidade="São Paulo",
            uf="SP",
        )
        Cep.objects.create(
            codigo_ibge="3550308",
            cep="04538132",
            logradouro="Avenida Brigadeiro Faria Lima",
            localidade="São Paulo",
            uf="SP",
        )

    def test_busca_parcial_e_tolerante_a_acentuacao(self):
        resultados = CepService.buscar_logradouro("avenida paulista")
        self.assertEqual(len(resultados), 1)
        self.assertEqual(resultados[0]["cep"], "01310930")

    def test_termo_vazio_retorna_lista_vazia(self):
        self.assertEqual(CepService.buscar_logradouro(""), [])
        self.assertEqual(CepService.buscar_logradouro(None), [])

    def test_limit_e_respeitado_e_limitado_ao_maximo(self):
        resultados = CepService.buscar_logradouro("avenida", limit=1)
        self.assertEqual(len(resultados), 1)
