"""
BO-08.5 (2026-08-07) — Worker periódico de consulta de status de autorizações
TISS "Em Análise" junto à Orizon (`tissSolicitacaoStatusAutorizacao_Operation`).

Cobre:
1. `services.consultar_elegibilidade_automatica` registra (idempotentemente)
   uma `TISSAutorizacaoPendente` quando a Orizon responde "Em Análise", e NÃO
   registra nada quando não há `numero_guia_prestador` para consultar depois.
2. `providers.orizon.consultar_status_autorizacao` — sucesso (autorizado/
   negado/em_analise) e falha de rede não vaza exceção.
3. `tiss/tasks.py::consultar_autorizacoes_pendentes_task` — consulta de
   status atualiza o registro local corretamente (autorizado/negado marca
   `resolvido=True`; em_analise mantém pendente).
4. Idempotência — rodar a task 2x seguidas sem novidade da operadora não
   duplica efeito (nem cria uma segunda linha, nem reprocessa uma pendência
   já resolvida).
5. Tratamento de erro — timeout/erro de rede da operadora não derruba a task
   nem lança exceção não tratada; a pendência continua para o próximo ciclo.
"""
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from clinics.models import Clinic, ClinicStatus, Plan, ProvisioningStatus

from . import providers
from .models import (
    TISSAutorizacaoPendente, TISSAutorizacaoSituacao, TISSElegibilidadeStatus,
    TISSGatewayProvider, TISSOperatorConfig,
)
from .orizon_autorize_client import OrizonAutorizeClientError
from .providers.base import StatusAutorizacaoResultado
from .services import consultar_elegibilidade_automatica
from .tasks import consultar_autorizacoes_pendentes_task, _consultar_uma_pendente_status


def _make_clinic():
    unique = uuid.uuid4().hex[:8]
    return Clinic.objects.create(
        name='Clínica Status Polling Teste',
        slug=f'clinica-status-{unique}',
        plan=Plan.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj=f'12.345.{unique[:3]}/0001-99',
        db_name=f'db_{unique}',
        db_user=f'u_{unique}',
        provisioning_status=ProvisioningStatus.PROVISIONED,
    )


def _make_config(clinic, gateway_provider=TISSGatewayProvider.ORIZON, registro_ans=None):
    op = TISSOperatorConfig.objects.create(
        clinic=clinic, nome_operadora='Orizon', registro_ans=registro_ans or uuid.uuid4().hex[:6],
        endpoint_url='https://wsp.hom.orizonbrasil.com.br:6213/tiss/v40300/tissSolicitacaoStatusAutorizacao',
        gateway_provider=gateway_provider,
    )
    op.set_login('teste001')
    op.set_senha('senha-teste')
    op.save()
    return op


def _make_pendente(clinic, operator_config, numero_guia_prestador='apt-001', **kwargs):
    defaults = {
        'clinic': clinic,
        'operator_config': operator_config,
        'numero_guia_prestador': numero_guia_prestador,
        'situacao': TISSAutorizacaoSituacao.EM_ANALISE,
    }
    defaults.update(kwargs)
    return TISSAutorizacaoPendente.objects.create(**defaults)


@override_settings(TISS_SOAP_MOCK=True)
class ConsultarElegibilidadeAutomaticaRegistraPendenciaTests(TestCase):
    """`services.consultar_elegibilidade_automatica` — gatilho da pendência."""

    def setUp(self):
        self.clinic = _make_clinic()
        self.op = _make_config(self.clinic)

    def test_em_analise_via_scenario_bruto_registra_pendencia_idempotente(self):
        with patch(
            'tiss.providers.orizon.orizon_solicitar_autorizacao',
        ) as mock_solicitar:
            from .orizon_autorize_client import AutorizacaoResult, SituacaoAutorizacao
            mock_solicitar.return_value = AutorizacaoResult(
                situacao=SituacaoAutorizacao.EM_ANALISE, numero_guia_operadora='',
                codigo_glosa='', descricao_glosa='', raw_response='<mock/>',
            )
            resultado = consultar_elegibilidade_automatica(
                self.clinic, self.op, numero_carteira='999', appointment_id='apt-001',
            )
            self.assertEqual(resultado.status_operacional, TISSElegibilidadeStatus.EM_ANALISE)

            # Idempotência: chamar de novo (retry do gateway) NÃO cria uma
            # segunda pendência para a mesma guia.
            consultar_elegibilidade_automatica(
                self.clinic, self.op, numero_carteira='999', appointment_id='apt-001',
            )

        pendentes = TISSAutorizacaoPendente.objects.filter(clinic=self.clinic)
        self.assertEqual(pendentes.count(), 1)
        pendente = pendentes.get()
        self.assertEqual(pendente.numero_guia_prestador, 'apt-001')
        self.assertEqual(pendente.situacao, TISSAutorizacaoSituacao.EM_ANALISE)
        self.assertFalse(pendente.resolvido)

    def test_helper_sem_numero_guia_prestador_nao_persiste(self):
        from .services import _registrar_autorizacao_pendente
        _registrar_autorizacao_pendente(self.clinic, self.op, appointment_id='', numero_guia_prestador='')
        self.assertFalse(TISSAutorizacaoPendente.objects.exists())

    def test_autorizado_nao_registra_pendencia(self):
        consultar_elegibilidade_automatica(
            self.clinic, self.op, numero_carteira='999', appointment_id='apt-002',
            mock_scenario='success',
        )
        self.assertFalse(TISSAutorizacaoPendente.objects.exists())


@override_settings(TISS_SOAP_MOCK=True)
class OrizonConsultarStatusAutorizacaoProviderTests(TestCase):
    def setUp(self):
        self.clinic = _make_clinic()
        self.op = _make_config(self.clinic)

    def test_consulta_autorizado(self):
        resultado = providers.orizon.consultar_status_autorizacao(
            self.clinic, self.op, numero_guia_prestador='apt-001', mock_scenario='autorizado',
        )
        self.assertIsInstance(resultado, StatusAutorizacaoResultado)
        self.assertTrue(resultado.sucesso)
        self.assertEqual(resultado.situacao, 'autorizado')
        self.assertEqual(resultado.numero_guia_operadora, 'MOCK-GUIA-OP-000001')

    def test_consulta_negado(self):
        resultado = providers.orizon.consultar_status_autorizacao(
            self.clinic, self.op, numero_guia_prestador='apt-001', mock_scenario='negado',
        )
        self.assertTrue(resultado.sucesso)
        self.assertEqual(resultado.situacao, 'negado')
        self.assertEqual(resultado.codigo_glosa, '3144')

    def test_consulta_ainda_em_analise(self):
        resultado = providers.orizon.consultar_status_autorizacao(
            self.clinic, self.op, numero_guia_prestador='apt-001', mock_scenario='em_analise',
        )
        self.assertTrue(resultado.sucesso)
        self.assertEqual(resultado.situacao, 'em_analise')

    def test_falha_de_rede_nao_vaza_excecao(self):
        with patch('tiss.providers.orizon.orizon_consultar_status_autorizacao') as mock_consultar:
            mock_consultar.side_effect = OrizonAutorizeClientError('soap_network_error')
            resultado = providers.orizon.consultar_status_autorizacao(
                self.clinic, self.op, numero_guia_prestador='apt-001',
            )
        self.assertFalse(resultado.sucesso)
        self.assertEqual(resultado.erro_code, 'soap_network_error')

    def test_sem_numero_guia_prestador_falha_local_sem_excecao(self):
        resultado = providers.orizon.consultar_status_autorizacao(
            self.clinic, self.op, numero_guia_prestador='',
        )
        self.assertFalse(resultado.sucesso)
        self.assertEqual(resultado.erro_code, 'xml_builder_failed')

    def test_capabilities_declara_consulta_status_true(self):
        self.assertTrue(providers.orizon.capabilities().consulta_status)


class OutrosProvidersConsultarStatusAutorizacaoTests(TestCase):
    def setUp(self):
        self.clinic = _make_clinic()

    def test_generico_ans_falha_alto_sem_fallback(self):
        op = _make_config(self.clinic, TISSGatewayProvider.GENERICO_ANS)
        with self.assertRaises(providers.OperacaoNaoSuportada):
            providers.generico_ans.consultar_status_autorizacao(self.clinic, op, 'apt-001')

    def test_desconhecido_falha_alto_sem_fallback(self):
        op = _make_config(self.clinic, TISSGatewayProvider.DESCONHECIDO)
        with self.assertRaises(providers.ProviderNaoConfirmado):
            providers.desconhecido.consultar_status_autorizacao(self.clinic, op, 'apt-001')


@override_settings(TISS_SOAP_MOCK=True)
class ConsultarAutorizacoesPendentesTaskTests(TestCase):
    """`tiss/tasks.py::consultar_autorizacoes_pendentes_task` — o worker em si."""

    def setUp(self):
        self.clinic = _make_clinic()
        self.op = _make_config(self.clinic)

    def _resultado(self, situacao, numero_guia_operadora='', sucesso=True, erro_code='', erro_mensagem=''):
        return StatusAutorizacaoResultado(
            sucesso=sucesso, situacao=situacao, numero_guia_operadora=numero_guia_operadora,
            erro_code=erro_code, erro_mensagem=erro_mensagem, raw_response='<mock/>',
        )

    def test_nenhuma_pendencia_nao_faz_nada(self):
        consultar_autorizacoes_pendentes_task.apply()
        self.assertEqual(TISSAutorizacaoPendente.objects.count(), 0)

    def test_autorizado_resolve_pendencia(self):
        pendente = _make_pendente(self.clinic, self.op)
        with patch(
            'tiss.providers._InstrumentedProvider.consultar_status_autorizacao',
        ) as mock_consultar:
            mock_consultar.return_value = self._resultado('autorizado', numero_guia_operadora='OP-999')
            consultar_autorizacoes_pendentes_task.apply()

        pendente.refresh_from_db()
        self.assertTrue(pendente.resolvido)
        self.assertEqual(pendente.situacao, TISSAutorizacaoSituacao.AUTORIZADO)
        self.assertEqual(pendente.numero_guia_operadora, 'OP-999')
        self.assertEqual(pendente.tentativas_consulta, 1)
        self.assertIsNotNone(pendente.ultima_consulta_em)

    def test_negado_resolve_pendencia_com_glosa(self):
        pendente = _make_pendente(self.clinic, self.op)
        with patch(
            'tiss.providers._InstrumentedProvider.consultar_status_autorizacao',
        ) as mock_consultar:
            resultado = self._resultado('negado')
            resultado.codigo_glosa = '3144'
            resultado.descricao_glosa = 'Negativa da operadora'
            mock_consultar.return_value = resultado
            consultar_autorizacoes_pendentes_task.apply()

        pendente.refresh_from_db()
        self.assertTrue(pendente.resolvido)
        self.assertEqual(pendente.situacao, TISSAutorizacaoSituacao.NEGADO)
        self.assertEqual(pendente.codigo_glosa, '3144')

    def test_ainda_em_analise_mantem_pendencia_nao_resolvida(self):
        pendente = _make_pendente(self.clinic, self.op)
        with patch(
            'tiss.providers._InstrumentedProvider.consultar_status_autorizacao',
        ) as mock_consultar:
            mock_consultar.return_value = self._resultado('em_analise')
            consultar_autorizacoes_pendentes_task.apply()

        pendente.refresh_from_db()
        self.assertFalse(pendente.resolvido)
        self.assertEqual(pendente.situacao, TISSAutorizacaoSituacao.EM_ANALISE)
        self.assertEqual(pendente.tentativas_consulta, 1)

    def test_ja_resolvida_nao_e_consultada_de_novo(self):
        _make_pendente(
            self.clinic, self.op, situacao=TISSAutorizacaoSituacao.AUTORIZADO, resolvido=True,
        )
        with patch(
            'tiss.providers._InstrumentedProvider.consultar_status_autorizacao',
        ) as mock_consultar:
            consultar_autorizacoes_pendentes_task.apply()
        mock_consultar.assert_not_called()

    def test_rodar_task_2x_sem_novidade_nao_duplica_efeito(self):
        """
        Requisito de idempotência explícito da task: 2 execuções seguidas sem
        novidade da operadora não geram efeito duplicado (nem segunda linha,
        nem duplo log de resolução).
        """
        pendente = _make_pendente(self.clinic, self.op)
        with patch(
            'tiss.providers._InstrumentedProvider.consultar_status_autorizacao',
        ) as mock_consultar:
            mock_consultar.return_value = self._resultado('autorizado', numero_guia_operadora='OP-999')
            consultar_autorizacoes_pendentes_task.apply()
            consultar_autorizacoes_pendentes_task.apply()

        # Segunda execução não encontra a pendência (já resolvido=True, fora
        # do filtro `resolvido=False`) — o mock só é chamado 1 vez no total.
        self.assertEqual(mock_consultar.call_count, 1)
        self.assertEqual(TISSAutorizacaoPendente.objects.filter(clinic=self.clinic).count(), 1)
        pendente.refresh_from_db()
        self.assertEqual(pendente.tentativas_consulta, 1)

    def test_erro_de_rede_nao_derruba_task_e_registra_erro_para_retry(self):
        pendente = _make_pendente(self.clinic, self.op)
        with patch(
            'tiss.providers._InstrumentedProvider.consultar_status_autorizacao',
        ) as mock_consultar:
            mock_consultar.side_effect = OrizonAutorizeClientError('soap_network_error')
            # A task NÃO deve propagar exceção — se propagar, este teste falha.
            consultar_autorizacoes_pendentes_task.apply()

        pendente.refresh_from_db()
        self.assertFalse(pendente.resolvido)
        self.assertEqual(pendente.situacao, TISSAutorizacaoSituacao.EM_ANALISE)
        self.assertEqual(pendente.tentativas_consulta, 1)
        self.assertIn('soap_network_error', pendente.ultimo_erro_consulta)

    def test_falha_de_consulta_sem_sucesso_registra_erro_sem_resolver(self):
        pendente = _make_pendente(self.clinic, self.op)
        with patch(
            'tiss.providers._InstrumentedProvider.consultar_status_autorizacao',
        ) as mock_consultar:
            mock_consultar.return_value = self._resultado(
                '', sucesso=False, erro_code='soap_fault', erro_mensagem='LoginInvalido: login inválido',
            )
            consultar_autorizacoes_pendentes_task.apply()

        pendente.refresh_from_db()
        self.assertFalse(pendente.resolvido)
        self.assertEqual(pendente.tentativas_consulta, 1)
        self.assertIn('LoginInvalido', pendente.ultimo_erro_consulta)

    def test_operadora_desativada_nao_derruba_task_nem_marca_resolvido(self):
        self.op.ativo = False
        self.op.save(update_fields=['ativo'])
        pendente = _make_pendente(self.clinic, self.op)

        consultar_autorizacoes_pendentes_task.apply()

        pendente.refresh_from_db()
        self.assertFalse(pendente.resolvido)
        self.assertEqual(pendente.tentativas_consulta, 1)
        self.assertIn('operadora_desativada', pendente.ultimo_erro_consulta)

    def test_provider_nao_suporta_consulta_status_nao_derruba_task(self):
        op_generico = _make_config(self.clinic, TISSGatewayProvider.GENERICO_ANS)
        pendente = _make_pendente(self.clinic, op_generico, numero_guia_prestador='apt-generico')

        # Não deve levantar OperacaoNaoSuportada para fora da task.
        consultar_autorizacoes_pendentes_task.apply()

        pendente.refresh_from_db()
        self.assertFalse(pendente.resolvido)

    def test_erro_inesperado_no_provider_nao_derruba_task(self):
        pendente = _make_pendente(self.clinic, self.op)
        with patch(
            'tiss.providers._InstrumentedProvider.consultar_status_autorizacao',
        ) as mock_consultar:
            mock_consultar.side_effect = RuntimeError('erro totalmente inesperado')
            consultar_autorizacoes_pendentes_task.apply()

        pendente.refresh_from_db()
        self.assertFalse(pendente.resolvido)
        self.assertEqual(pendente.tentativas_consulta, 1)
        self.assertIn('erro_inesperado', pendente.ultimo_erro_consulta)

    def test_varias_pendencias_isolamento_de_falha_por_item(self):
        """
        Uma pendência com erro não pode impedir que as demais do mesmo ciclo
        sejam consultadas — cobertura explícita do requisito de resiliência.
        """
        pendente_ok = _make_pendente(self.clinic, self.op, numero_guia_prestador='apt-ok')
        pendente_com_erro = _make_pendente(self.clinic, self.op, numero_guia_prestador='apt-erro')

        def side_effect(clinic, operator_config, numero_guia_prestador, numero_guia_operadora=''):
            if numero_guia_prestador == 'apt-erro':
                raise RuntimeError('falha simulada só nesta pendência')
            return self._resultado('autorizado', numero_guia_operadora='OP-OK')

        with patch(
            'tiss.providers._InstrumentedProvider.consultar_status_autorizacao',
            side_effect=side_effect,
        ):
            consultar_autorizacoes_pendentes_task.apply()

        pendente_ok.refresh_from_db()
        pendente_com_erro.refresh_from_db()
        self.assertTrue(pendente_ok.resolvido)
        self.assertFalse(pendente_com_erro.resolvido)
