from unittest.mock import patch, MagicMock

from django.core.cache import cache
from django.conf import settings
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from zipcode.models import Cep


@override_settings(ROOT_URLCONF='zipcode.urls')
class CepSearchViewTestCase(APITestCase):
    """
    Endpoint é público (AllowAny) — dado de referência geográfica
    (IBGE/ViaCEP), não PII, não específico de clínica (ver docstring
    de CepSearchView).

    NOTA: usa `ROOT_URLCONF='zipcode.urls'` (em vez de `reverse('cep_search')`
    contra o urlconf real do projeto) por conveniência dos testes de
    comportamento da view — `syncro_backoffice/urls.py` já inclui
    `zipcode.urls` em `path('api/ceps/', ...)` (corrigido em 2026-08-02,
    era o achado reportado na revisão de código: o endpoint não existia
    em produção até essa linha ser adicionada). O throttle, por depender
    de scope/rate configurados em `settings.py` e de reverse() correto,
    é testado à parte contra o urlconf real — ver `CepSearchThrottleTests`
    abaixo.
    """

    def setUp(self):
        # Throttle (ReferenceDataRateThrottle) usa o cache padrão pra contar
        # requisições por IP — sem limpar entre testes, o contador acumula
        # e testes depois do 30º neste TestCase começam a receber 429 em
        # vez do status que estão de fato verificando.
        cache.clear()
        self.client = APIClient()
        self.url = '/'
        Cep.objects.create(
            codigo_ibge="3550308",
            cep="01310930",
            logradouro="Avenida Paulista",
            bairro="Bela Vista",
            localidade="São Paulo",
            uf="SP",
        )

    def test_nao_exige_autenticacao(self):
        response = self.client.get(self.url, {'cep': '01310930'})
        self.assertEqual(response.status_code, 200)

    def test_sem_parametro_logradouro_ou_cep_retorna_400(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 400)
        self.assertIn('erro', response.json())

    def test_busca_por_cep_cache_hit_retorna_registro(self):
        response = self.client.get(self.url, {'cep': '01310930'})
        self.assertEqual(response.status_code, 200)
        dados = response.json()
        self.assertEqual(dados['cep'], '01310930')
        self.assertEqual(dados['logradouro'], 'Avenida Paulista')

    def test_busca_por_cep_inexistente_e_sem_provider_disponivel_retorna_dict_vazio(self):
        with patch('requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_get.return_value = mock_response

            response = self.client.get(self.url, {'cep': '99999999'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {})

    def test_busca_por_logradouro_retorna_lista(self):
        response = self.client.get(self.url, {'logradouro': 'Paulista'})
        self.assertEqual(response.status_code, 200)
        dados = response.json()
        self.assertIsInstance(dados, list)
        self.assertEqual(dados[0]['cep'], '01310930')

    def test_falha_de_rede_na_api_externa_nao_derruba_o_endpoint(self):
        """
        Uma falha/timeout do provider externo não pode virar 500 pro
        gateway Go — deve degradar para resposta vazia (200).
        """
        import requests
        with patch('requests.get', side_effect=requests.exceptions.Timeout("boom")):
            response = self.client.get(self.url, {'cep': '12345678'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {})


class CepSearchThrottleTests(APITestCase):
    """
    Achado da revisão de código do app `zipcode` (2026-08-02): o endpoint é
    público (AllowAny) sem throttle dedicado, herdando só o
    AnonRateThrottle default (60/min por IP) — insuficiente para
    diferenciar uso legítimo do gateway (cache-aside, maioria dos CEPs
    repete) de um cliente mandando CEP inválido/aleatório em volume, que
    sempre dá cache miss e vira N chamadas à API externa do ViaCEP.
    Mesma classe de risco já resolvida para os endpoints TUSS/ANS em
    BACFF-AVULSA-03 (`tiss/tests_reference_data.py`, mesmo padrão de teste
    replicado aqui) — reaproveita `ReferenceDataRateThrottle` (30/min).

    NOTA (mesma observação de `tiss/tests_reference_data.py`): a rate real
    é lida dinamicamente de settings.py — SimpleRateThrottle lê
    THROTTLE_RATES de api_settings no import do módulo, então
    override_settings(REST_FRAMEWORK=...) em tempo de teste não teria
    efeito. Testa contra o urlconf real do projeto (não `zipcode.urls`),
    já que `path('api/ceps/', include('zipcode.urls'))` está registrado.
    """

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        rate = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['reference_data']
        num_requests, period = rate.split('/')
        assert period == 'minute', "teste assume rate por minuto; ajustar se a unidade mudar"
        self.limit = int(num_requests)

    def _lookup(self):
        return self.client.get('/api/ceps/', {'logradouro': 'Paulista'})

    def test_calls_within_limit_succeed(self):
        for _ in range(self.limit):
            self.assertEqual(self._lookup().status_code, status.HTTP_200_OK)

    def test_call_beyond_limit_returns_429(self):
        for _ in range(self.limit):
            self.assertEqual(self._lookup().status_code, status.HTTP_200_OK)

        response = self._lookup()
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
