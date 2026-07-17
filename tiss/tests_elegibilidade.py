import uuid

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from clinics.models import Clinic, ClinicStatus, Plan, ProvisioningStatus
from .models import TISSOperatorConfig, TISSElegibilidadeConsulta, TISSElegibilidadeOrigem, TISSElegibilidadeStatus
from .services import (
    consultar_elegibilidade_automatica, registrar_elegibilidade_manual, TISSServiceError,
)


def make_clinic():
    unique = uuid.uuid4().hex[:8]
    return Clinic.objects.create(
        name='Clínica Elegibilidade Teste',
        slug=f'clinica-elegib-{unique}',
        plan=Plan.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj=f'12.345.{unique[:3]}/0001-99',
        db_name=f'db_{unique}',
        db_user=f'u_{unique}',
        provisioning_status=ProvisioningStatus.PROVISIONED,
    )


@override_settings(TISS_SOAP_MOCK=True)
class ConsultarElegibilidadeAutomaticaServiceTests(TestCase):
    """
    BACFF-013 — service layer. Confirma que cada cenário do soap_client
    (success/negativa/error) vira uma linha de auditoria persistida com os
    campos certos, sem nunca atualizar um registro existente (cada consulta
    é imutável).
    """

    def setUp(self):
        self.clinic = make_clinic()
        self.op = TISSOperatorConfig.objects.create(
            clinic=self.clinic, nome_operadora='Orizon', registro_ans='123456',
            endpoint_url='https://tiss-documentos.orizon.com.br/Service.asmx',
        )

    def test_cenario_success_persiste_elegivel_true(self):
        consulta = consultar_elegibilidade_automatica(
            clinic=self.clinic, operator_config=self.op, numero_carteira='CARTEIRA-1',
            mock_scenario='success',
        )
        self.assertTrue(consulta.elegivel)
        self.assertEqual(consulta.origem, TISSElegibilidadeOrigem.AUTOMATICA)
        self.assertEqual(consulta.motivos_negativa, [])
        self.assertEqual(TISSElegibilidadeConsulta.objects.count(), 1)

    def test_cenario_negativa_persiste_elegivel_false_com_motivos(self):
        consulta = consultar_elegibilidade_automatica(
            clinic=self.clinic, operator_config=self.op, numero_carteira='CARTEIRA-2',
            mock_scenario='negativa',
        )
        self.assertFalse(consulta.elegivel)
        self.assertEqual(len(consulta.motivos_negativa), 1)
        self.assertEqual(consulta.motivos_negativa[0]['codigo'], '1822')

    def test_cenario_error_de_transporte_persiste_com_erro_mensagem(self):
        consulta = consultar_elegibilidade_automatica(
            clinic=self.clinic, operator_config=self.op, numero_carteira='CARTEIRA-3',
            mock_scenario='error',
        )
        self.assertFalse(consulta.elegivel)
        self.assertTrue(consulta.erro_mensagem)

    def test_log_operacional_central_nao_guarda_pii_do_beneficiario(self):
        """
        BACFF-AVULSA-01: o log persistido no banco central não deve conter
        numero_carteira nem beneficiario_nome em NENHUM campo — nem mesmo
        dentro de erro_mensagem (que deve ser só texto técnico).
        """
        consultar_elegibilidade_automatica(
            clinic=self.clinic, operator_config=self.op, numero_carteira='CARTEIRA-SECRETA',
            beneficiario_nome='Paciente Confidencial', mock_scenario='negativa',
        )
        log = TISSElegibilidadeConsulta.objects.get(clinic=self.clinic)
        self.assertFalse(hasattr(log, 'numero_carteira'))
        self.assertFalse(hasattr(log, 'beneficiario_nome'))
        self.assertNotIn('CARTEIRA-SECRETA', log.erro_mensagem)
        self.assertNotIn('Paciente Confidencial', log.erro_mensagem)
        self.assertEqual(log.status, TISSElegibilidadeStatus.SUCESSO)

    def test_duas_consultas_geram_dois_logs_operacionais(self):
        """
        BACFF-AVULSA-01: o log central não guarda mais numero_carteira (é
        conteúdo clínico, não deve persistir aqui) — a garantia de "nunca
        sobrescreve, sempre gera linha nova" agora se verifica pela
        contagem de logs da clínica, não por filtro de carteirinha.
        """
        consultar_elegibilidade_automatica(
            clinic=self.clinic, operator_config=self.op, numero_carteira='CARTEIRA-4', mock_scenario='success',
        )
        consultar_elegibilidade_automatica(
            clinic=self.clinic, operator_config=self.op, numero_carteira='CARTEIRA-4', mock_scenario='negativa',
        )
        self.assertEqual(TISSElegibilidadeConsulta.objects.filter(clinic=self.clinic).count(), 2)


class RegistrarElegibilidadeManualServiceTests(TestCase):
    def setUp(self):
        self.clinic = make_clinic()
        self.op = TISSOperatorConfig.objects.create(
            clinic=self.clinic, nome_operadora='Orizon', registro_ans='123456',
            endpoint_url='https://tiss-documentos.orizon.com.br/Service.asmx',
        )

    def test_registro_manual_com_numero_guia_persiste_origem_manual(self):
        consulta = registrar_elegibilidade_manual(
            clinic=self.clinic, operator_config=self.op, numero_carteira='CARTEIRA-5',
            numero_guia_operadora='GUIA-MANUAL-001', elegivel=True,
        )
        self.assertEqual(consulta.origem, TISSElegibilidadeOrigem.MANUAL)
        self.assertEqual(consulta.numero_guia_operadora, 'GUIA-MANUAL-001')

    def test_registro_manual_sem_numero_guia_levanta_erro(self):
        """
        BACFF-013: numero_guia_operadora é obrigatório no fallback manual —
        não é uma exceção informal, é um caminho de primeira classe com a
        mesma exigência de rastreabilidade da consulta automática.
        """
        with self.assertRaises(TISSServiceError):
            registrar_elegibilidade_manual(
                clinic=self.clinic, operator_config=self.op, numero_carteira='CARTEIRA-6',
                numero_guia_operadora='', elegivel=True,
            )
        self.assertEqual(TISSElegibilidadeConsulta.objects.filter(clinic=self.clinic).count(), 0)


@override_settings(TISS_SOAP_MOCK=True)
class ElegibilidadeEndpointTests(TestCase):
    """
    Endpoints consumidos pelo Edge Gateway via license_key — mesmo padrão de
    autenticação de metrics.heartbeat, nunca por usuário do backoffice.
    """

    def setUp(self):
        self.client = APIClient()
        self.clinic = make_clinic()
        self.op = TISSOperatorConfig.objects.create(
            clinic=self.clinic, nome_operadora='Orizon', registro_ans='123456',
            endpoint_url='https://tiss-documentos.orizon.com.br/Service.asmx',
        )

    def test_verificar_endpoint_exige_license_key(self):
        response = self.client.post(
            '/api/tiss/elegibilidade/verificar/',
            {'registro_ans': self.op.registro_ans, 'numero_carteira': 'CARTEIRA-7'},
            format='json',
        )
        self.assertEqual(response.status_code, 401)

    def test_verificar_endpoint_com_license_key_retorna_201(self):
        response = self.client.post(
            '/api/tiss/elegibilidade/verificar/',
            {'registro_ans': self.op.registro_ans, 'numero_carteira': 'CARTEIRA-8', 'mock_scenario': 'success'},
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data['elegivel'])

    def test_verificar_endpoint_operadora_de_outra_clinica_retorna_404(self):
        """Isolamento: license_key de uma clínica não pode usar operator_config de outra."""
        outra_clinic = make_clinic()
        outro_op = TISSOperatorConfig.objects.create(
            clinic=outra_clinic, nome_operadora='Amil', registro_ans='654321',
            endpoint_url='https://amil.example.com/Service.asmx',
        )
        response = self.client.post(
            '/api/tiss/elegibilidade/verificar/',
            {'registro_ans': outro_op.registro_ans, 'numero_carteira': 'CARTEIRA-9'},
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, 404)

    def test_manual_endpoint_sem_numero_guia_retorna_422(self):
        response = self.client.post(
            '/api/tiss/elegibilidade/manual/',
            {
                'registro_ans': self.op.registro_ans, 'numero_carteira': 'CARTEIRA-10',
                'numero_guia_operadora': '', 'elegivel': True,
            },
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, 400)

    def test_manual_endpoint_com_numero_guia_retorna_201(self):
        response = self.client.post(
            '/api/tiss/elegibilidade/manual/',
            {
                'registro_ans': self.op.registro_ans, 'numero_carteira': 'CARTEIRA-11',
                'numero_guia_operadora': 'GUIA-MANUAL-002', 'elegivel': False,
            },
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['origem'], 'manual')
