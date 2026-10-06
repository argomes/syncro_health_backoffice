import datetime

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from accounts.models import SupportUser
from holidays.admin import FeriadoAdmin, FeriadoBuscaAdmin
from holidays.models import Feriado, FeriadoBusca

CHANGELIST_URL = reverse('admin:holidays_feriado_changelist')
ADD_URL = reverse('admin:holidays_feriado_add')


def _make_ano_novo() -> Feriado:
    return Feriado.objects.create(date=datetime.date(2026, 1, 1), name='Ano Novo', type='NACIONAL')


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

    def test_admins_usam_model_admin_do_unfold(self):
        """Com admin.ModelAdmin padrão o Unfold não renderiza o botão de adicionar."""
        self.assertTrue(issubclass(FeriadoAdmin, UnfoldModelAdmin))
        self.assertTrue(issubclass(FeriadoBuscaAdmin, UnfoldModelAdmin))


class FeriadoChangelistAddButtonTest(TestCase):
    """Regressão: o fundador não achava como cadastrar feriado manualmente."""

    def setUp(self):
        # Lista com registros: no empty-state o Unfold mostra "Create" mesmo
        # com o ModelAdmin padrão, o que mascarava o bug.
        _make_ano_novo()

    def test_superuser_ve_link_de_adicionar(self):
        user = SupportUser.objects.create_superuser('fundador', 'f@syncro.test', 'x', role='admin')
        self.client.force_login(user)

        response = self.client.get(CHANGELIST_URL)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{ADD_URL}"')

    def test_staff_com_permissoes_de_feriado_ve_link_de_adicionar(self):
        user = SupportUser.objects.create_user('staff', 's@syncro.test', 'x', is_staff=True, role='admin')
        user.user_permissions.set(
            Permission.objects.filter(content_type__app_label='holidays', codename__in=['view_feriado', 'add_feriado'])
        )
        self.client.force_login(user)

        response = self.client.get(CHANGELIST_URL)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{ADD_URL}"')

    def test_staff_so_com_view_nao_ve_link_de_adicionar(self):
        user = SupportUser.objects.create_user('leitor', 'l@syncro.test', 'x', is_staff=True)
        user.user_permissions.set(
            Permission.objects.filter(content_type__app_label='holidays', codename='view_feriado')
        )
        self.client.force_login(user)

        response = self.client.get(CHANGELIST_URL)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f'href="{ADD_URL}"')


class FeriadoAddViewTest(TestCase):
    def setUp(self):
        user = SupportUser.objects.create_superuser('fundador', 'f@syncro.test', 'x', role='admin')
        self.client.force_login(user)

    def _post(self, **data):
        payload = {'date': '2026-11-19', 'name': 'Aniversário de Osasco', 'type': 'MUNICIPAL',
                   'description': '', 'uf': '', 'ibge_code': '3534401'}
        payload.update(data)
        return self.client.post(ADD_URL, payload)

    def test_formulario_nao_pede_ano(self):
        response = self.client.get(ADD_URL)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('year', response.context['adminform'].form.fields)
        self.assertContains(response, '3534401')  # help text "Ex.: Osasco = 3534401"

    def test_salva_municipal_valido_com_ano_e_uf_derivados(self):
        response = self._post()

        self.assertEqual(response.status_code, 302)
        feriado = Feriado.objects.get(type='MUNICIPAL', ibge_code='3534401')
        self.assertEqual(feriado.year, 2026)
        self.assertEqual(feriado.uf, 'SP')
        self.assertEqual(feriado.date, datetime.date(2026, 11, 19))

    def test_rejeita_municipal_sem_ibge(self):
        response = self._post(ibge_code='')

        self.assertEqual(response.status_code, 200)
        self.assertIn('ibge_code', response.context['adminform'].form.errors)
        self.assertFalse(Feriado.objects.exists())

    def test_rejeita_municipal_com_uf_incoerente(self):
        response = self._post(uf='RJ')

        self.assertEqual(response.status_code, 200)
        self.assertIn('uf', response.context['adminform'].form.errors)
        self.assertFalse(Feriado.objects.exists())

    def test_rejeita_estadual_sem_uf(self):
        response = self._post(type='ESTADUAL', ibge_code='', name='Revolução Constitucionalista', date='2026-07-09')

        self.assertEqual(response.status_code, 200)
        self.assertIn('uf', response.context['adminform'].form.errors)

    def test_salva_estadual_sem_ibge(self):
        response = self._post(type='ESTADUAL', uf='SP', ibge_code='3534401',
                              name='Revolução Constitucionalista', date='2026-07-09')

        self.assertEqual(response.status_code, 302)
        feriado = Feriado.objects.get(type='ESTADUAL')
        self.assertEqual(feriado.uf, 'SP')
        self.assertIsNone(feriado.ibge_code)

    def test_rejeita_nacional_duplicado(self):
        _make_ano_novo()

        response = self._post(type='NACIONAL', date='2026-01-01', name='Confraternização', ibge_code='')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Feriado.objects.filter(type='NACIONAL').count(), 1)


class FeriadoModelRulesTest(TestCase):
    def test_nacional_limpa_uf_e_ibge(self):
        feriado = Feriado(date=datetime.date(2026, 4, 21), name='Tiradentes', type='NACIONAL',
                          uf='SP', ibge_code='3534401')
        feriado.full_clean()
        self.assertIsNone(feriado.uf)
        self.assertIsNone(feriado.ibge_code)
        self.assertEqual(feriado.year, 2026)

    def test_municipal_ibge_com_formato_invalido(self):
        feriado = Feriado(date=datetime.date(2026, 11, 19), name='X', type='MUNICIPAL', ibge_code='35344')
        with self.assertRaises(ValidationError) as ctx:
            feriado.full_clean()
        self.assertIn('ibge_code', ctx.exception.message_dict)

    def test_save_sincroniza_ano_com_a_data(self):
        feriado = Feriado.objects.create(date=datetime.date(2027, 1, 1), name='Ano Novo', type='NACIONAL', year=1999)
        self.assertEqual(feriado.year, 2027)

    def test_busca_admin_renderiza(self):
        FeriadoBusca.objects.create(ibge_code='3534401', year=2026)
        user = SupportUser.objects.create_superuser('fundador', 'f@syncro.test', 'x')
        self.client.force_login(user)
        response = self.client.get(reverse('admin:holidays_feriadobusca_changelist'))
        self.assertEqual(response.status_code, 200)
