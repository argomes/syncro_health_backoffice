import datetime
from django.test import TestCase
from unittest.mock import patch, MagicMock
from requests.exceptions import Timeout
from holidays.providers import ApiHolidayProvider


def _make_feriado(nome: str, data: str = "01/01/2026", tipo: str = "NACIONAL") -> dict:
    return {
        "id": "7288fa7a-c9da-4435-980f-23cf62eccd1d",
        "data": data,
        "nome": nome,
        "tipo": tipo,
        "descricao": "descricao qualquer",
        "uf": None,
        "codigo_ibge": None,
        "bancario": True,
    }


def _make_response(status_code: int, feriados=None, page=1, total_pages=1):
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = {
        "cidade": {"ibge": 3550308, "nome": "São Paulo", "uf": "SP"},
        "ano": "2026",
        "feriados": feriados or [],
        "meta": {
            "total": len(feriados or []),
            "page": page,
            "per_page": 100,
            "total_pages": total_pages,
        },
    }
    return mock_response


class HolidayProviderTestCase(TestCase):
    """Testa se o Adapter da API converte os dados externos corretamente"""

    def setUp(self):
        self.provider = ApiHolidayProvider(api_key="test_token", api_url="https://feriadosapi.com/api")

    def test_url_usa_endpoint_correto(self):
        # Regressão do Bug 1: o endpoint antigo (/v1/municipio/{ibge}) nunca
        # devolvia a chave "feriados".
        self.assertEqual(
            self.provider.api_url,
            "https://feriadosapi.com/api/v1/feriados/cidade/{ibge}",
        )

    @patch('requests.get')
    def test_provider_parse_dados_corretamente_1_pagina(self, mock_get):
        mock_get.return_value = _make_response(200, feriados=[_make_feriado("Ano Novo")])

        resultado = self.provider.find_municipal_holidays(ibge_code="3534401", year=2026)

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]['nome'], "Ano Novo")
        self.assertEqual(resultado[0]['data'], datetime.date(2026, 1, 1))
        self.assertEqual(resultado[0]['uf'], None)
        mock_get.assert_called_once()
        self.assertEqual(mock_get.call_args.kwargs['params']['limit'], 100)
        self.assertEqual(mock_get.call_args.kwargs['params']['page'], 1)

    @patch('requests.get')
    def test_provider_pagina_multiplas_paginas(self, mock_get):
        # Regressão do Bug 2: o provider tinha que fazer loop até
        # meta.page == meta.total_pages, acumulando todas as páginas.
        pagina_1 = _make_response(
            200,
            feriados=[_make_feriado("Feriado 1", "01/01/2026"), _make_feriado("Feriado 2", "02/01/2026")],
            page=1,
            total_pages=3,
        )
        pagina_2 = _make_response(
            200,
            feriados=[_make_feriado("Feriado 3", "03/01/2026")],
            page=2,
            total_pages=3,
        )
        pagina_3 = _make_response(
            200,
            feriados=[_make_feriado("Feriado 4", "04/01/2026")],
            page=3,
            total_pages=3,
        )
        mock_get.side_effect = [pagina_1, pagina_2, pagina_3]

        resultado = self.provider.find_municipal_holidays(ibge_code="3534401", year=2026)

        self.assertEqual(len(resultado), 4)
        self.assertEqual(mock_get.call_count, 3)
        self.assertEqual(mock_get.call_args_list[1].kwargs['params']['page'], 2)
        self.assertEqual(mock_get.call_args_list[2].kwargs['params']['page'], 3)

    @patch('requests.get')
    def test_provider_erro_http_todos_endpoints_retorna_lista_vazia(self, mock_get):
        # Cidade falha (cota estourada) E o fallback nacional/estadual
        # também falha -> só aí a lista fica realmente vazia.
        mock_get.return_value = _make_response(500)

        resultado = self.provider.find_municipal_holidays(ibge_code="3534401", year=2026)

        self.assertEqual(resultado, [])
        self.assertEqual(mock_get.call_count, 3)  # cidade + nacionais + estado, todos falhando

    @patch('requests.get')
    def test_provider_timeout_todos_endpoints_retorna_lista_vazia(self, mock_get):
        mock_get.side_effect = Timeout("timed out")

        resultado = self.provider.find_municipal_holidays(ibge_code="3534401", year=2026)

        self.assertEqual(resultado, [])

    @patch('requests.get')
    def test_provider_cidade_falha_fallback_nacional_estadual_funciona(self, mock_get):
        # Cenário real do bug: cota mensal da API estourou pra município
        # não-capital (endpoint /cidade retorna erro), mas nacional e
        # estadual são gratuitos e devem preencher o vazio.
        resposta_cidade_erro = _make_response(500)
        resposta_nacional = _make_response(
            200, feriados=[_make_feriado("Confraternização Universal", "01/01/2026", tipo="NACIONAL")]
        )
        resposta_estadual = _make_response(
            200, feriados=[_make_feriado("Revolução Constitucionalista", "09/07/2026", tipo="ESTADUAL")]
        )
        mock_get.side_effect = [resposta_cidade_erro, resposta_nacional, resposta_estadual]

        # 3534401 = Osasco/SP, código IBGE começa com "35" (SP), não é capital.
        resultado = self.provider.find_municipal_holidays(ibge_code="3534401", year=2026)

        self.assertEqual(len(resultado), 2)
        nomes = {f['nome'] for f in resultado}
        self.assertEqual(nomes, {"Confraternização Universal", "Revolução Constitucionalista"})

        # Confirma que a 2ª e 3ª chamadas foram pros endpoints gratuitos corretos.
        self.assertIn("/v1/feriados/nacionais", mock_get.call_args_list[1].args[0])
        self.assertIn("/v1/feriados/estado/SP", mock_get.call_args_list[2].args[0])

    @patch('requests.get')
    def test_provider_cidade_falha_ibge_invalido_busca_so_nacional(self, mock_get):
        # Se o prefixo do código IBGE não é reconhecido, não há UF pra
        # derivar -> busca só o endpoint nacional, sem quebrar.
        resposta_cidade_erro = _make_response(500)
        resposta_nacional = _make_response(
            200, feriados=[_make_feriado("Confraternização Universal")]
        )
        mock_get.side_effect = [resposta_cidade_erro, resposta_nacional]

        resultado = self.provider.find_municipal_holidays(ibge_code="0000000", year=2026)

        self.assertEqual(len(resultado), 1)
        self.assertEqual(mock_get.call_count, 2)

    @patch('requests.get')
    def test_provider_erro_em_pagina_subsequente_retorna_parcial(self, mock_get):
        pagina_1 = _make_response(
            200,
            feriados=[_make_feriado("Feriado 1", "01/01/2026")],
            page=1,
            total_pages=2,
        )
        mock_get.side_effect = [pagina_1, Timeout("timed out")]

        resultado = self.provider.find_municipal_holidays(ibge_code="3534401", year=2026)

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]['nome'], "Feriado 1")