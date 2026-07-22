import datetime
from django.test import TestCase
from unittest.mock import patch, MagicMock
from holidays.models import Feriado
from holidays.services import HolidayService

class HolidayServiceCacheTestCase(TestCase):
    """Testa a regra de negócio do Cache-Aside (Lazy Loading) do Service"""

    def setUp(self):
        # Cria um feriado nacional padrão que deve ser retornado sempre
        Feriado.objects.create(
            date=datetime.date(2026, 1, 1),
            name="Confraternização Universal",
            type="NACIONAL",
            year=2026,
            description="Feriado Nacional do dia 1º de Janeiro, conhecido como Ano Novo.",
            uf=None,
            ibge_code=None
        )

    @patch.object(HolidayService, '_get_provider')
    def test_cache_miss_chama_api_e_salva_no_banco(self, mock_get_provider):
        """Cenário 1: Se não houver cache local, consome e salva no banco"""
        mock_provider = MagicMock()
        mock_provider.find_municipal_holidays.return_value = [
            {"data": datetime.date(2026, 6, 13), "nome": "Santo Antônio", "tipo": "MUNICIPAL", "uf": "SP"}
        ]
        mock_get_provider.return_value = mock_provider

        codigo_ibge = "3534401"
        resultado = HolidayService.find_calendar_complet(ibge_code=codigo_ibge, year=2026)

        mock_provider.find_municipal_holidays.assert_called_once_with(codigo_ibge, 2026)
        self.assertEqual(resultado.count(), 2)  # 1 Nacional + 1 Municipal
        self.assertTrue(Feriado.objects.filter(ibge_code=codigo_ibge, type='MUNICIPAL').exists())

    @patch.object(HolidayService, '_get_provider')
    def test_cache_hit_retorna_direto_do_banco_sem_chamar_api(self, mock_get_provider):
        """Cenário 2: Se o cache já existir, ignora completamente a API externa"""
        codigo_ibge = "3534401"
        Feriado.objects.create(
            date=datetime.date(2026, 6, 13),
            name="Santo Antônio",
            type="MUNICIPAL",
            ibge_code=codigo_ibge,
            year=2026,
            uf="SP"
        )

        mock_provider = MagicMock()
        mock_get_provider.return_value = mock_provider

        resultado = HolidayService.find_calendar_complet(ibge_code=codigo_ibge, year=2026)

        mock_provider.find_municipal_holidays.assert_not_called()
        self.assertEqual(resultado.count(), 2)