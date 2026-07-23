from django.test import TestCase
from django.contrib.admin.sites import AdminSite
from holidays.models import Feriado
from holidays.admin import FeriadoAdmin

class HolidayAdminTestCase(TestCase):
    """Garante a integridade visual da interface admin do módulo de feriados"""

    def setUp(self):
        self.site = AdminSite()
        self.admin = FeriadoAdmin(Feriado, self.site)

    def test_configuracoes_admin_estao_mapeadas_corretamente(self):
        """Assegura filtros e buscas para acelerar o suporte operacional"""
        self.assertIn('type', self.admin.list_filter)
        self.assertIn('year', self.admin.list_filter)
        self.assertIn('name', self.admin.search_fields)
        self.assertIn('ibge_code', self.admin.search_fields)