import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from clinics.models import Clinic, ClinicStatus, Plan, ProvisioningStatus
from .models import (
    TISSOperatorConfig, TISSElegibilidadeConsulta, TISSElegibilidadeOrigem, TISSElegibilidadeStatus,
    TISSGatewayProvider,
)
from .services import (
    consultar_elegibilidade_automatica, registrar_elegibilidade_manual, TISSServiceError,
    ElegibilidadeRespostaCompleta,
)
from .soap_client import ElegibilidadeResult
from .orizon_autorize_client import (
    AutorizacaoResult, SOAPFaultResult as OrizonSOAPFaultResult, SituacaoAutorizacao,
    OrizonAutorizeClientError,
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
        # D3: `gateway_provider` passou a ser explícito. Estes testes
        # exercitam o caminho genérico ANS, então declaram-no — antes eles
        # dependiam do default silencioso do model, que é exatamente o que
        # o D3 eliminou.
        self.op = TISSOperatorConfig.objects.create(
            clinic=self.clinic, nome_operadora='Orizon', registro_ans='123456',
            endpoint_url='https://tiss-documentos.orizon.com.br/Service.asmx',
            gateway_provider=TISSGatewayProvider.GENERICO_ANS,
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


@override_settings(TISS_SOAP_MOCK=True)
class ConsultarElegibilidadeOperadoraDesativadaTests(TestCase):
    """
    BACFF-AVULSA-12 (issue #45): `ativo` precisa ser checado ANTES de
    qualquer I/O de rede também no caminho de elegibilidade — mesmo bug do
    envio de lote, mesmo fix.
    """

    def setUp(self):
        self.clinic = make_clinic()
        # D3: `gateway_provider` passou a ser explícito. Estes testes
        # exercitam o caminho genérico ANS, então declaram-no — antes eles
        # dependiam do default silencioso do model, que é exatamente o que
        # o D3 eliminou.
        self.op = TISSOperatorConfig.objects.create(
            clinic=self.clinic, nome_operadora='Orizon', registro_ans='123456',
            endpoint_url='https://tiss-documentos.orizon.com.br/Service.asmx',
            gateway_provider=TISSGatewayProvider.GENERICO_ANS,
        )

    def test_operadora_ativa_prossegue_sem_regressao(self):
        consulta = consultar_elegibilidade_automatica(
            clinic=self.clinic, operator_config=self.op, numero_carteira='CARTEIRA-ATIVA',
            mock_scenario='success',
        )
        self.assertTrue(consulta.elegivel)

    @patch('tiss.providers.generico_ans.soap_verificar_elegibilidade')
    def test_operadora_inativa_bloqueia_antes_de_qualquer_io_de_rede(self, mock_soap_verificar):
        self.op.ativo = False
        self.op.save(update_fields=['ativo'])

        with self.assertRaises(TISSServiceError) as ctx:
            consultar_elegibilidade_automatica(
                clinic=self.clinic, operator_config=self.op, numero_carteira='CARTEIRA-INATIVA',
                mock_scenario='success',
            )

        self.assertEqual(ctx.exception.code, 'operadora_desativada')
        mock_soap_verificar.assert_not_called()
        # Nenhum log operacional deve ter sido criado — o bloqueio acontece
        # antes de qualquer tentativa de consulta.
        self.assertEqual(TISSElegibilidadeConsulta.objects.filter(clinic=self.clinic).count(), 0)


class RegistrarElegibilidadeManualServiceTests(TestCase):
    def setUp(self):
        self.clinic = make_clinic()
        # Deliberadamente SEM `gateway_provider` explícito: fica no novo
        # default `desconhecido` (D3). Isso faz esta classe inteira provar,
        # de quebra, que o registro manual funciona mesmo numa config cujo
        # dialeto nunca foi confirmado — D2 e D3 se sustentando juntos.
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

    def test_registro_manual_continua_funcionando_com_operadora_desativada(self):
        """
        BACFF-AVULSA-12: desativar a operadora não deve bloquear o registro
        manual — ele não faz I/O de rede com a operadora, é a recepcionista
        registrando o que já obteve por telefone/portal. Decisão de escopo
        do hotfix: desativar a integração automática não pode travar o
        trabalho manual da recepção.
        """
        self.op.ativo = False
        self.op.save(update_fields=['ativo'])

        consulta = registrar_elegibilidade_manual(
            clinic=self.clinic, operator_config=self.op, numero_carteira='CARTEIRA-MANUAL-INATIVA',
            numero_guia_operadora='GUIA-MANUAL-002', elegivel=True,
        )
        self.assertEqual(consulta.origem, TISSElegibilidadeOrigem.MANUAL)

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
            gateway_provider=TISSGatewayProvider.GENERICO_ANS,
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


@override_settings(TISS_SOAP_MOCK=True)
class ConsultarElegibilidadeDispatchGatewayProviderTests(TestCase):
    """
    BACFF-014 — cobre o ponto de despacho em `consultar_elegibilidade_automatica`:
    a escolha do client SOAP (genérico ANS vs. Orizon) deve seguir
    `operator_config.gateway_provider`, nunca hardcoded. Antes desta task o
    client Orizon existia mas nunca era chamado pelo fluxo real.
    """

    def setUp(self):
        self.clinic = make_clinic()

    def _make_operator(self, gateway_provider):
        op = TISSOperatorConfig.objects.create(
            clinic=self.clinic, nome_operadora='Orizon', registro_ans='123456',
            endpoint_url='https://tiss-documentos.orizon.com.br/Service.asmx',
            gateway_provider=gateway_provider,
        )
        op.set_login('login-teste')
        op.set_senha('senha-teste')
        return op

    def test_default_sem_gateway_provider_explicito_e_desconhecido_e_bloqueia(self):
        """
        D3 (substitui `test_default_sem_gateway_provider_explicito_usa_generico_ans`):
        o default do model deixou de ser `generico_ans`. Uma config criada
        sem escolha explícita não tem dialeto confirmado com a operadora, e
        o comportamento correto passou a ser falhar claramente em vez de
        tentar o dialeto genérico — que nenhuma operadora nossa confirmou
        aceitar, e que sequer envia credencial.

        O teste afirma as duas metades que importam: falha com código
        explícito, E nenhum client SOAP é sequer invocado.
        """
        op = TISSOperatorConfig.objects.create(
            clinic=self.clinic, nome_operadora='Amil', registro_ans='654321',
            endpoint_url='https://amil.example.com/Service.asmx',
        )
        self.assertEqual(op.gateway_provider, TISSGatewayProvider.DESCONHECIDO)

        with patch('tiss.providers.generico_ans.soap_verificar_elegibilidade') as mock_generico, \
                patch('tiss.providers.orizon.orizon_solicitar_autorizacao') as mock_orizon:
            with self.assertRaises(TISSServiceError) as ctx:
                consultar_elegibilidade_automatica(
                    clinic=self.clinic, operator_config=op, numero_carteira='CARTEIRA-D1',
                )

        self.assertEqual(ctx.exception.code, 'provider_nao_confirmado')
        mock_generico.assert_not_called()
        mock_orizon.assert_not_called()
        # Nenhum log operacional: não houve consulta a registrar.
        self.assertEqual(TISSElegibilidadeConsulta.objects.filter(clinic=self.clinic).count(), 0)

    def test_gateway_provider_generico_ans_explicito_chama_client_generico(self):
        op = self._make_operator(TISSGatewayProvider.GENERICO_ANS)
        with patch('tiss.providers.generico_ans.soap_verificar_elegibilidade') as mock_generico, \
                patch('tiss.providers.orizon.orizon_solicitar_autorizacao') as mock_orizon:
            mock_generico.return_value = ElegibilidadeResult(
                elegivel=False, numero_carteira='CARTEIRA-D2', nome_beneficiario='',
                motivos_negativa=[('1822', 'Carteira vencida')], raw_response='<xml/>',
            )
            consulta = consultar_elegibilidade_automatica(
                clinic=self.clinic, operator_config=op, numero_carteira='CARTEIRA-D2',
            )
        mock_generico.assert_called_once()
        mock_orizon.assert_not_called()
        self.assertFalse(consulta.elegivel)
        self.assertEqual(consulta.motivos_negativa[0]['codigo'], '1822')

    def test_gateway_provider_orizon_chama_client_orizon_e_normaliza_autorizado(self):
        op = self._make_operator(TISSGatewayProvider.ORIZON)
        with patch('tiss.providers.orizon.orizon_solicitar_autorizacao') as mock_orizon, \
                patch('tiss.providers.generico_ans.soap_verificar_elegibilidade') as mock_generico:
            mock_orizon.return_value = AutorizacaoResult(
                situacao=SituacaoAutorizacao.AUTORIZADO,
                numero_guia_operadora='GUIA-OP-999',
                codigo_glosa='', descricao_glosa='', raw_response='<xml/>',
            )
            consulta = consultar_elegibilidade_automatica(
                clinic=self.clinic, operator_config=op, numero_carteira='CARTEIRA-D3',
                beneficiario_nome='Fulano',
            )
        mock_orizon.assert_called_once()
        mock_generico.assert_not_called()
        self.assertIsInstance(consulta, ElegibilidadeRespostaCompleta)
        self.assertTrue(consulta.elegivel)
        self.assertEqual(consulta.numero_guia_operadora, 'GUIA-OP-999')
        self.assertEqual(consulta.beneficiario_nome, 'Fulano')
        self.assertEqual(consulta.motivos_negativa, [])
        log = TISSElegibilidadeConsulta.objects.get(clinic=self.clinic)
        self.assertEqual(log.status, TISSElegibilidadeStatus.SUCESSO)

    def test_gateway_provider_orizon_normaliza_negado_com_motivos(self):
        op = self._make_operator(TISSGatewayProvider.ORIZON)
        with patch('tiss.providers.orizon.orizon_solicitar_autorizacao') as mock_orizon:
            mock_orizon.return_value = AutorizacaoResult(
                situacao=SituacaoAutorizacao.NEGADO,
                numero_guia_operadora='', codigo_glosa='3144',
                descricao_glosa='Negativa mock', raw_response='<xml/>',
            )
            consulta = consultar_elegibilidade_automatica(
                clinic=self.clinic, operator_config=op, numero_carteira='CARTEIRA-D4',
            )
        self.assertFalse(consulta.elegivel)
        self.assertEqual(consulta.motivos_negativa, [{'codigo': '3144', 'descricao': 'Negativa mock'}])

    def test_gateway_provider_orizon_normaliza_fault_como_falha_operadora(self):
        op = self._make_operator(TISSGatewayProvider.ORIZON)
        with patch('tiss.providers.orizon.orizon_solicitar_autorizacao') as mock_orizon:
            mock_orizon.return_value = OrizonSOAPFaultResult(
                codigo_erro='LoginInvalido', descricao_erro='Login ou senha inválidos', raw_response='<xml/>',
            )
            consulta = consultar_elegibilidade_automatica(
                clinic=self.clinic, operator_config=op, numero_carteira='CARTEIRA-D5',
            )
        self.assertFalse(consulta.elegivel)
        self.assertIn('LoginInvalido', consulta.erro_mensagem)
        log = TISSElegibilidadeConsulta.objects.get(clinic=self.clinic)
        self.assertEqual(log.status, TISSElegibilidadeStatus.FALHA_OPERADORA)
        self.assertNotIn('CARTEIRA-D5', log.erro_mensagem)

    def test_gateway_provider_orizon_falha_de_transporte_nao_propaga_excecao(self):
        op = self._make_operator(TISSGatewayProvider.ORIZON)
        with patch('tiss.providers.orizon.orizon_solicitar_autorizacao') as mock_orizon:
            mock_orizon.side_effect = OrizonAutorizeClientError('soap_network_error')
            consulta = consultar_elegibilidade_automatica(
                clinic=self.clinic, operator_config=op, numero_carteira='CARTEIRA-D6',
            )
        self.assertFalse(consulta.elegivel)
        log = TISSElegibilidadeConsulta.objects.get(clinic=self.clinic)
        self.assertEqual(log.status, TISSElegibilidadeStatus.FALHA_TRANSPORTE)
