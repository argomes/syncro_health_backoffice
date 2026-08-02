from unittest.mock import patch, MagicMock

from django.test import TestCase

from zipcode.providers import ApiCepProvider


class ApiCepProviderTestCase(TestCase):
    """Testa se o Adapter da API converte os dados externos corretamente"""

    @patch('requests.get')
    def test_provider_parse_dados_corretamente(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "cep": "01310-930",
            "logradouro": "Avenida Paulista",
            "complemento": "",
            "bairro": "Bela Vista",
            "localidade": "São Paulo",
            "uf": "SP",
            "ibge": "3550308",
            "gia": "1004",
            "ddd": "11",
            "siafi": "7107",
        }
        mock_get.return_value = mock_response

        provider = ApiCepProvider(api_url="https://viacep.com.br")
        resultado = provider.find_cep("01310930")

        mock_get.assert_called_once_with(
            "https://viacep.com.br/ws/01310930/json/", timeout=8
        )
        self.assertEqual(resultado["logradouro"], "Avenida Paulista")
        self.assertEqual(resultado["localidade"], "São Paulo")
        self.assertEqual(resultado["ibge"], "3550308")

    @patch('requests.get')
    def test_cep_inexistente_retorna_dict_vazio(self, mock_get):
        """ViaCEP responde 200 com {"erro": true} para CEP inexistente."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"erro": True}
        mock_get.return_value = mock_response

        provider = ApiCepProvider(api_url="https://viacep.com.br")
        resultado = provider.find_cep("00000000")

        self.assertEqual(resultado, {})

    @patch('requests.get')
    def test_status_diferente_de_200_retorna_dict_vazio(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        provider = ApiCepProvider(api_url="https://viacep.com.br")
        resultado = provider.find_cep("01310930")

        self.assertEqual(resultado, {})

    @patch('requests.get')
    def test_timeout_ou_erro_de_rede_retorna_dict_vazio_sem_propagar_excecao(self, mock_get):
        import requests
        mock_get.side_effect = requests.exceptions.Timeout("boom")

        provider = ApiCepProvider(api_url="https://viacep.com.br")
        resultado = provider.find_cep("01310930")

        self.assertEqual(resultado, {})
