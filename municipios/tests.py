from django.urls import reverse
from rest_framework.test import APITestCase, APIClient

from municipios.models import Municipio
from municipios.timezones import TIMEZONE_BY_UF, resolve_timezone


class MunicipioModelTests(APITestCase):
    def test_codigo_ibge_e_unico(self):
        Municipio.objects.create(codigo_ibge="3550308", nome="São Paulo", uf="SP")
        with self.assertRaises(Exception):
            Municipio.objects.create(codigo_ibge="3550308", nome="São Paulo Duplicado", uf="SP")


class ResolveTimezoneTests(APITestCase):
    """
    Cobre os 4 fusos fixos do Brasil (sem DST desde 2019) mapeados a
    partir da UF, incluindo o caso especial de Fernando de Noronha, que
    não segue o fuso do resto do seu UF (PE).
    """

    def test_todas_as_27_ufs_estao_mapeadas(self):
        ufs_brasil = {
            'AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MT',
            'MS', 'MG', 'PA', 'PB', 'PR', 'PE', 'PI', 'RJ', 'RN', 'RS', 'RO',
            'RR', 'SC', 'SP', 'SE', 'TO',
        }
        self.assertEqual(set(TIMEZONE_BY_UF.keys()), ufs_brasil)

    def test_sao_paulo_resolve_america_sao_paulo(self):
        self.assertEqual(resolve_timezone('SP'), 'America/Sao_Paulo')

    def test_amazonas_resolve_america_manaus(self):
        self.assertEqual(resolve_timezone('AM'), 'America/Manaus')

    def test_acre_resolve_america_rio_branco(self):
        self.assertEqual(resolve_timezone('AC'), 'America/Rio_Branco')

    def test_uf_desconhecida_retorna_none(self):
        self.assertIsNone(resolve_timezone('XX'))

    def test_fernando_de_noronha_resolve_america_noronha_por_override_ibge(self):
        # UF de Fernando de Noronha é PE (America/Sao_Paulo), mas o
        # arquipélago em si usa America/Noronha — override por código IBGE.
        self.assertEqual(resolve_timezone('PE', '2605459'), 'America/Noronha')

    def test_resto_de_pernambuco_resolve_america_sao_paulo(self):
        self.assertEqual(resolve_timezone('PE', '2611606'), 'America/Sao_Paulo')  # Recife


class MunicipioSearchViewTests(APITestCase):
    """
    Endpoint é público (AllowAny) — dado de referência geográfica, não
    PII, não específico de clínica (ver docstring de MunicipioSearchView).
    """

    def setUp(self):
        self.client = APIClient()
        self.url = reverse('municipio_search')
        Municipio.objects.create(codigo_ibge="3550308", nome="São Paulo", uf="SP")
        Municipio.objects.create(codigo_ibge="3304557", nome="Rio de Janeiro", uf="RJ")
        Municipio.objects.create(codigo_ibge="1302603", nome="Manaus", uf="AM")
        Municipio.objects.create(codigo_ibge="2605459", nome="Fernando de Noronha", uf="PE")

    def test_nao_exige_autenticacao(self):
        response = self.client.get(self.url, {'q': 'Manaus'})
        self.assertEqual(response.status_code, 200)

    def test_parametro_q_obrigatorio(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 400)

    def test_busca_por_nome_retorna_municipio_correto(self):
        response = self.client.get(self.url, {'q': 'Manaus'})
        self.assertEqual(response.status_code, 200)
        dados = response.json()
        self.assertEqual(len(dados), 1)
        self.assertEqual(dados[0]['codigo_ibge'], '1302603')
        self.assertEqual(dados[0]['nome'], 'Manaus')
        self.assertEqual(dados[0]['uf'], 'AM')

    def test_busca_e_case_insensitive_e_parcial(self):
        response = self.client.get(self.url, {'q': 'rio de jan'})
        dados = response.json()
        self.assertEqual(len(dados), 1)
        self.assertEqual(dados[0]['nome'], 'Rio de Janeiro')

    def test_resposta_inclui_timezone_resolvido(self):
        response = self.client.get(self.url, {'q': 'São Paulo'})
        dados = response.json()
        self.assertEqual(dados[0]['timezone'], 'America/Sao_Paulo')

        response = self.client.get(self.url, {'q': 'Manaus'})
        dados = response.json()
        self.assertEqual(dados[0]['timezone'], 'America/Manaus')

    def test_timezone_de_fernando_de_noronha_usa_override(self):
        response = self.client.get(self.url, {'q': 'Fernando de Noronha'})
        dados = response.json()
        self.assertEqual(dados[0]['timezone'], 'America/Noronha')

    def test_busca_sem_resultado_retorna_lista_vazia(self):
        response = self.client.get(self.url, {'q': 'MunicipioInexistenteXYZ'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
