import datetime
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from holidays.models import Feriado, FeriadoBusca
from holidays.providers import HolidayFetchResult
from holidays.services import HolidayService

API_URL = "https://feriadosapi.com/api"
OSASCO = "3534401"  # SP


def _api_response(status_code: int, feriados: list[dict] | None = None) -> MagicMock:
    """Resposta fake de `requests.get` no formato da feriadosapi."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {
        "feriados": feriados or [],
        "meta": {"page": 1, "total_pages": 1},
    }
    return resp


def _api_feriado(nome: str, data: str, tipo: str, uf: str | None = None) -> dict:
    return {"data": data, "nome": nome, "tipo": tipo, "uf": uf, "descricao": None}


CALENDARIO_OSASCO = [
    _api_feriado("Confraternização Universal", "01/01/2026", "NACIONAL"),
    _api_feriado("Revolução Constitucionalista", "09/07/2026", "ESTADUAL", "SP"),
    _api_feriado("Aniversário de Osasco", "19/02/2026", "MUNICIPAL", "SP"),
]


@override_settings(API_HOLIDAY_KEY="chave-de-teste", API_HOLIDAY=API_URL)
class HolidayServiceCacheTestCase(TestCase):
    """Regras do cache-aside do HolidayService (API sempre mockada)."""

    def setUp(self) -> None:
        Feriado.objects.create(
            date=datetime.date(2026, 1, 1),
            name="Confraternização Universal",
            type="NACIONAL",
            year=2026,
        )

    @patch.object(HolidayService, '_get_provider')
    def test_cache_miss_chama_api_e_salva_no_banco(self, mock_get_provider: MagicMock) -> None:
        mock_provider = MagicMock()
        mock_provider.fetch_holidays.return_value = HolidayFetchResult(
            feriados=[{"data": datetime.date(2026, 6, 13), "nome": "Santo Antônio", "tipo": "MUNICIPAL", "uf": "SP"}],
            complete=True,
        )
        mock_get_provider.return_value = mock_provider

        resultado = HolidayService.find_calendar_complet(ibge_code=OSASCO, year=2026)

        mock_provider.fetch_holidays.assert_called_once_with(OSASCO, 2026)
        self.assertEqual(resultado.count(), 2)  # 1 Nacional + 1 Municipal
        self.assertTrue(Feriado.objects.filter(ibge_code=OSASCO, type='MUNICIPAL').exists())
        self.assertTrue(FeriadoBusca.objects.filter(ibge_code=OSASCO, year=2026).exists())

    @patch.object(HolidayService, '_get_provider')
    def test_cache_hit_retorna_direto_do_banco_sem_chamar_api(self, mock_get_provider: MagicMock) -> None:
        Feriado.objects.create(
            date=datetime.date(2026, 6, 13), name="Santo Antônio", type="MUNICIPAL",
            ibge_code=OSASCO, year=2026, uf="SP",
        )
        FeriadoBusca.objects.create(ibge_code=OSASCO, year=2026)

        resultado = HolidayService.find_calendar_complet(ibge_code=OSASCO, year=2026)

        mock_get_provider.assert_not_called()
        self.assertEqual(resultado.count(), 2)

    @patch('requests.get')
    def test_estaduais_sao_gravados_com_uf_e_retornados(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _api_response(200, CALENDARIO_OSASCO)

        resultado = HolidayService.find_calendar_complet(ibge_code=OSASCO, year=2026)

        tipos = sorted(resultado.values_list('type', flat=True))
        self.assertEqual(tipos, ['ESTADUAL', 'MUNICIPAL', 'NACIONAL'])
        estadual = Feriado.objects.get(type='ESTADUAL')
        self.assertEqual(estadual.uf, 'SP')
        self.assertIsNone(estadual.ibge_code)
        # Nacional já existia no setUp: não pode duplicar (ibge_code NULL
        # escapa da unique_together).
        self.assertEqual(Feriado.objects.filter(type='NACIONAL').count(), 1)

    @patch('requests.get')
    def test_estadual_de_outra_uf_nao_e_retornado(self, mock_get: MagicMock) -> None:
        Feriado.objects.create(
            date=datetime.date(2026, 4, 23), name="São Jorge", type="ESTADUAL", uf="RJ", year=2026,
        )
        mock_get.return_value = _api_response(200, CALENDARIO_OSASCO)

        resultado = HolidayService.find_calendar_complet(ibge_code=OSASCO, year=2026)

        self.assertNotIn("São Jorge", list(resultado.values_list('name', flat=True)))

    @patch('requests.get')
    def test_municipio_sem_feriado_municipal_nao_rechama_api(self, mock_get: MagicMock) -> None:
        # Regressão: o critério antigo (Feriado com ibge_code existe) nunca
        # era satisfeito para município sem feriado municipal próprio.
        mock_get.return_value = _api_response(200, CALENDARIO_OSASCO[:2])

        HolidayService.find_calendar_complet(ibge_code=OSASCO, year=2026)
        HolidayService.find_calendar_complet(ibge_code=OSASCO, year=2026)

        self.assertEqual(mock_get.call_count, 1)
        self.assertFalse(Feriado.objects.filter(type='MUNICIPAL').exists())

    @patch('requests.get')
    def test_falha_da_api_loga_e_nao_marca_como_buscado(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _api_response(500)

        with self.assertLogs('holidays', level='WARNING') as logs:
            resultado = HolidayService.find_calendar_complet(ibge_code=OSASCO, year=2026)

        saida = "\n".join(logs.output)
        self.assertIn("status 500", saida)
        self.assertIn(OSASCO, saida)
        self.assertNotIn("chave-de-teste", saida)
        self.assertFalse(FeriadoBusca.objects.exists())
        self.assertEqual(Feriado.objects.count(), 1)  # só o do setUp
        self.assertEqual(resultado.count(), 1)

        # Próxima chamada tenta de novo, e agora com sucesso.
        mock_get.reset_mock()
        mock_get.return_value = _api_response(200, CALENDARIO_OSASCO)
        resultado = HolidayService.find_calendar_complet(ibge_code=OSASCO, year=2026)
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(resultado.count(), 3)
        self.assertTrue(FeriadoBusca.objects.filter(ibge_code=OSASCO, year=2026).exists())

    @patch('requests.get')
    def test_fallback_nacional_estadual_grava_mas_nao_marca(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = [
            _api_response(429),
            _api_response(200, [CALENDARIO_OSASCO[0]]),
            _api_response(200, [CALENDARIO_OSASCO[1]]),
        ]

        with self.assertLogs('holidays', level='WARNING'):
            resultado = HolidayService.find_calendar_complet(ibge_code=OSASCO, year=2026)

        self.assertEqual(resultado.count(), 2)  # nacional + estadual
        self.assertFalse(FeriadoBusca.objects.exists())

    @override_settings(API_HOLIDAY_KEY="1231312")
    @patch('requests.get')
    def test_key_placeholder_loga_e_nao_chama_api(self, mock_get: MagicMock) -> None:
        with self.assertLogs('holidays', level='ERROR') as logs:
            HolidayService.find_calendar_complet(ibge_code=OSASCO, year=2026)

        mock_get.assert_not_called()
        self.assertIn("API_HOLIDAY_KEY", "\n".join(logs.output))
        self.assertNotIn("1231312", "\n".join(logs.output))
        self.assertFalse(FeriadoBusca.objects.exists())

    @override_settings(API_HOLIDAY_KEY="")
    @patch('requests.get')
    def test_key_ausente_loga_e_nao_chama_api(self, mock_get: MagicMock) -> None:
        with self.assertLogs('holidays', level='ERROR'):
            HolidayService.find_calendar_complet(ibge_code=OSASCO, year=2026)
        mock_get.assert_not_called()


@override_settings(API_HOLIDAY_KEY="chave-de-teste", API_HOLIDAY=API_URL)
class PreloadHolidaysCommandTestCase(TestCase):
    """`manage.py preload_holidays`."""

    @patch('requests.get')
    def test_command_idempotente(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _api_response(200, CALENDARIO_OSASCO)

        out = StringIO()
        call_command('preload_holidays', '--year', '2026', '--ibge', OSASCO, stdout=out)
        call_command('preload_holidays', '--year', '2026', '--ibge', OSASCO, stdout=out)

        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(Feriado.objects.count(), 3)
        self.assertEqual(FeriadoBusca.objects.count(), 1)
        self.assertIn("[OK] 3534401/2026: 3 feriados", out.getvalue())
        self.assertIn("[CACHE] 3534401/2026", out.getvalue())

    @patch('requests.get')
    def test_command_falha_sai_com_erro(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _api_response(500)

        with self.assertLogs('holidays', level='WARNING'), self.assertRaises(CommandError):
            call_command('preload_holidays', '--year', '2026', '--ibge', OSASCO, stdout=StringIO(), stderr=StringIO())
        self.assertFalse(FeriadoBusca.objects.exists())

    def test_command_rejeita_ibge_invalido(self) -> None:
        with self.assertRaises(CommandError):
            call_command('preload_holidays', '--year', '2026', '--ibge', '123')

    @override_settings(API_HOLIDAY_KEY="")
    @patch('requests.get')
    def test_command_sem_key_falha_sem_chamar_api(self, mock_get: MagicMock) -> None:
        with self.assertRaises(CommandError):
            call_command('preload_holidays', '--year', '2026', '--ibge', OSASCO)
        mock_get.assert_not_called()
