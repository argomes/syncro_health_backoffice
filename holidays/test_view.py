import datetime

from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from clinics.tests import make_clinic
from holidays.models import Feriado, FeriadoBusca


class HolidayViewTestCase(APITestCase):
    """Testa o comportamento das rotas HTTP do backoffice.

    EDGW-060: este endpoint é consumido pelo worker do gateway (chamada
    máquina-a-máquina via X-License-Key, sem sessão de usuário) — mesmo
    mecanismo já usado pelos endpoints de referência do gateway
    (ver tiss/tests_reference_data.py). Por isso a clínica de teste aqui
    reaproveita o helper `make_clinic` de clinics/tests.py em vez de criar
    um usuário autenticado por JWT.
    """

    def setUp(self):
        self.client = APIClient()
        self.clinic = make_clinic()

        # Popula o banco com um feriado existente
        Feriado.objects.create(
            date=datetime.date(2026, 1, 1),
            name="Ano Novo",
            type="NACIONAL",
            year=2026,
            description="Feriado Nacional do dia 1º de Janeiro, conhecido como Ano Novo.",
            uf=None,
            ibge_code=None
        )
        # URL fictícia configurada nas rotas do projeto
        self.url = reverse('listar_feriados_clinica')

    def test_rejeita_requisicao_sem_autenticacao(self):
        """Bloqueia o acesso de quem não possui credenciais válidas"""
        response = self.client.get(self.url, {'ibge': '3534401', 'ano': '2026'})
        self.assertEqual(response.status_code, 401)

    def test_autoriza_requisicao_autenticada_e_valida_parametros(self):
        """Exige o envio dos parâmetros obrigatórios por query string"""
        auth_headers = {'HTTP_X_LICENSE_KEY': str(self.clinic.license_key)}

        # Requisição sem os parâmetros obrigatórios
        response_sem_parametros = self.client.get(self.url, **auth_headers)
        self.assertEqual(response_sem_parametros.status_code, 400)

        # Requisição correta (Simulando que o cache local do IBGE já existe para não chamar API mockada)
        Feriado.objects.create(
            date=datetime.date(2026, 6, 13),
            name="Feriado Municipal",
            type="MUNICIPAL",
            ibge_code="3534401",
            year=2026,
            description="Feriado Municipal de teste.",
            uf="SP"
        )
        FeriadoBusca.objects.create(ibge_code="3534401", year=2026)
        response_valido = self.client.get(self.url, {'ibge': '3534401', 'year': '2026'}, **auth_headers)
        self.assertEqual(response_valido.status_code, 200)
        self.assertEqual(len(response_valido.json()), 2)


class FeriadoManualNoEndpointTest(APITestCase):
    """Feriado cadastrado à mão no portal precisa chegar ao gateway.

    Caso real: a feriadosapi.com não devolveu nenhum municipal de Osasco
    (3534401). Com a busca já marcada (FeriadoBusca), o endpoint responde
    do banco — sem tocar na API externa — e deve incluir os manuais.
    """

    def setUp(self):
        self.client = APIClient()
        self.clinic = make_clinic()
        self.auth = {'HTTP_X_LICENSE_KEY': str(self.clinic.license_key)}
        self.url = reverse('listar_feriados_clinica')
        FeriadoBusca.objects.create(ibge_code='3534401', year=2026)

    def _cadastrar(self, **campos):
        feriado = Feriado(**campos)
        feriado.full_clean()  # mesmo caminho de validação do form do admin
        feriado.save()
        return feriado

    def _get(self, ibge='3534401'):
        response = self.client.get(self.url, {'ibge': ibge, 'year': '2026'}, **self.auth)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_municipal_manual_sai_para_o_ibge_certo(self):
        self._cadastrar(date=datetime.date(2026, 2, 19), name='Aniversário de Osasco',
                        type='MUNICIPAL', ibge_code='3534401')

        dados = self._get()

        self.assertIn(
            {'data': '2026-02-19', 'nome': 'Aniversário de Osasco', 'tipo': 'MUNICIPAL', 'uf': 'SP'}, dados,
        )

    def test_municipal_manual_nao_vaza_para_outro_municipio(self):
        self._cadastrar(date=datetime.date(2026, 2, 19), name='Aniversário de Osasco',
                        type='MUNICIPAL', ibge_code='3534401')
        FeriadoBusca.objects.create(ibge_code='3550308', year=2026)  # São Paulo capital

        nomes = [f['nome'] for f in self._get(ibge='3550308')]

        self.assertNotIn('Aniversário de Osasco', nomes)

    def test_estadual_manual_sai_para_municipio_da_uf(self):
        self._cadastrar(date=datetime.date(2026, 7, 9), name='Revolução Constitucionalista',
                        type='ESTADUAL', uf='SP')

        dados = self._get()

        self.assertIn(
            {'data': '2026-07-09', 'nome': 'Revolução Constitucionalista', 'tipo': 'ESTADUAL', 'uf': 'SP'}, dados,
        )
