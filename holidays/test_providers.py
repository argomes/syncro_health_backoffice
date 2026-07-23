import datetime
from django.test import TestCase
from unittest.mock import patch, MagicMock
from holidays.providers import ApiHolidayProvider

class HolidayProviderTestCase(TestCase):
    """Testa se o Adapter da API converte os dados externos corretamente"""

    @patch('requests.get')
    def test_provider_parse_dados_corretamente(self, mock_get):
        # Simula a resposta bruta em JSON que a FeriadosAPI.com entrega
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "cidade": {
                "ibge": 3550308,
                "nome": "São Paulo",
                "uf": "SP"
            },
            "feriados": [
                {
                    "id": "7288fa7a-c9da-4435-980f-23cf62eccd1d",
                    "data": "01/01/2026",
                    "nome": "Ano Novo",
                    "tipo": "NACIONAL",
                    "descricao": "O Ano-Novo ou Réveillon é um evento que acontece quando uma cultura celebra o fim de um ano e o começo do próximo. A celebração do evento é também chamada Réveillon, termo oriundo do verbo francês réveiller, que em português significa DESPERTAR",
                    "uf": None,
                    "codigo_ibge": None,
                    "bancario": True
                }
            ]
        }
        mock_get.return_value = mock_response

        # Executa o método do provider
        provider = ApiHolidayProvider(api_key="test_token", api_url="https://feriadosapi.com/api")
        resultado = provider.find_municipal_holidays(ibge_code="3534401", year=2026)

        # Asserts de integridade de tipo e valor
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]['nome'], "Ano Novo")
        self.assertEqual(resultado[0]['data'], datetime.date(2026, 1, 1))
        self.assertEqual(resultado[0]['uf'], None)