import datetime
from django.urls import reverse
from rest_framework.test import APITestCase, APIClient
from clinics.tests import make_clinic
from holidays.models import Feriado


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
        response_valido = self.client.get(self.url, {'ibge': '3534401', 'year': '2026'}, **auth_headers)
        self.assertEqual(response_valido.status_code, 200)
        self.assertEqual(len(response_valido.json()), 2)
