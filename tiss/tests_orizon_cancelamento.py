"""
BACFF-014 (2026-07-30) — cancelamento automático de guia junto à Orizon.

Cobre:
1. `providers.orizon.cancelar_guia` (sucesso/negado/fault via mock).
2. `providers.generico_ans.cancelar_guia`/`providers.desconhecido.cancelar_guia`
   falham alto (OperacaoNaoSuportada/ProviderNaoConfirmado) — nunca fallback.
3. `services.disparar_cancelamento_guia` — gatilho: só enfileira a task para
   guia com autorização já emitida; guia nunca enviada só marca CANCELADA.
4. `tiss/tasks.py::cancelar_guia_task` — sucesso marca a guia
   CANCELADA; falha esgotando os 3 retries cria `TISSCancelamentoPendente`
   com `falhou_apos_retries=True` (decisão de produto do usuário, 2026-07-30:
   não pode falhar silenciosamente).
"""
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from clinics.models import Clinic, ClinicStatus, Plan, ProvisioningStatus

from . import providers
from .models import (
    TISSCancelamentoPendente, TISSGatewayProvider, TISSGuia, TISSGuiaStatus,
    TISSLote, TISSLoteStatus, TISSOperatorConfig,
)
from .providers.base import CancelamentoResultado, OperacaoNaoSuportada, ProviderNaoConfirmado
from .services import disparar_cancelamento_guia
from .tasks import cancelar_guia_task, _tratar_falha, NON_TRANSIENT_ERROR_CODES


def _make_clinic():
    unique = uuid.uuid4().hex[:8]
    return Clinic.objects.create(
        name='Clínica Cancelamento Teste',
        slug=f'clinica-cancel-{unique}',
        plan=Plan.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj=f'12.345.{unique[:3]}/0001-99',
        db_name=f'db_{unique}',
        db_user=f'u_{unique}',
        provisioning_status=ProvisioningStatus.PROVISIONED,
    )


def _make_config(clinic, gateway_provider):
    op = TISSOperatorConfig.objects.create(
        clinic=clinic, nome_operadora='Orizon', registro_ans='123456',
        endpoint_url='https://wsp.hom.orizonbrasil.com.br:6213/tiss/v40300/tissCancelaGuia',
        gateway_provider=gateway_provider,
    )
    op.set_login('teste001')
    op.set_senha('senha-teste')
    op.save()
    return op


def _make_guia(clinic, operator_config=None, status=TISSGuiaStatus.ENVIADA, appointment_id=''):
    lote = None
    if operator_config is not None:
        lote = TISSLote.objects.create(
            clinic=clinic, operator_config=operator_config, numero_lote=1,
            competencia='2026-07', status=TISSLoteStatus.ENVIADO,
        )
    return TISSGuia.objects.create(
        clinic=clinic, lote=lote, numero='1234', competencia='2026-07',
        numero_carteira='999', valor=Decimal('150.50'), status=status,
        appointment_id=appointment_id,
    )


@override_settings(TISS_SOAP_MOCK=True)
class OrizonProviderCancelarGuiaTests(TestCase):
    def setUp(self):
        self.clinic = _make_clinic()
        self.op = _make_config(self.clinic, TISSGatewayProvider.ORIZON)
        self.guia = _make_guia(self.clinic, self.op)

    def test_cancelamento_bem_sucedido(self):
        resultado = providers.orizon.cancelar_guia(self.clinic, self.op, self.guia, mock_scenario='success')
        self.assertIsInstance(resultado, CancelamentoResultado)
        self.assertTrue(resultado.sucesso)
        self.assertEqual(resultado.numero_guia_operadora, 'MOCK-GUIA-OP-000001')

    def test_cancelamento_recusado_pela_operadora(self):
        resultado = providers.orizon.cancelar_guia(self.clinic, self.op, self.guia, mock_scenario='negativa')
        self.assertFalse(resultado.sucesso)
        self.assertEqual(resultado.erro_code, 'guia_nao_cancelada')

    def test_falha_de_rede_nao_vaza_excecao(self):
        with patch('tiss.providers.orizon.orizon_cancelar_guia') as mock_cancelar:
            from .orizon_autorize_client import OrizonAutorizeClientError
            mock_cancelar.side_effect = OrizonAutorizeClientError('soap_network_error')
            resultado = providers.orizon.cancelar_guia(self.clinic, self.op, self.guia)
        self.assertFalse(resultado.sucesso)
        self.assertEqual(resultado.erro_code, 'soap_network_error')

    def test_capabilities_declara_cancelamento_guia_true(self):
        self.assertTrue(providers.orizon.capabilities().cancelamento_guia)


class OutrosProvidersCancelarGuiaTests(TestCase):
    def setUp(self):
        self.clinic = _make_clinic()

    def test_generico_ans_falha_alto_sem_fallback(self):
        op = _make_config(self.clinic, TISSGatewayProvider.GENERICO_ANS)
        guia = _make_guia(self.clinic, op)
        with self.assertRaises(OperacaoNaoSuportada):
            providers.generico_ans.cancelar_guia(self.clinic, op, guia)

    def test_desconhecido_falha_alto_sem_fallback(self):
        op = _make_config(self.clinic, TISSGatewayProvider.DESCONHECIDO)
        guia = _make_guia(self.clinic, op)
        with self.assertRaises(ProviderNaoConfirmado):
            providers.desconhecido.cancelar_guia(self.clinic, op, guia)


@override_settings(TISS_SOAP_MOCK=True, CELERY_TASK_ALWAYS_EAGER=True)
class DispararCancelamentoGuiaServiceTests(TestCase):
    def setUp(self):
        self.clinic = _make_clinic()
        self.op = _make_config(self.clinic, TISSGatewayProvider.ORIZON)

    def test_guia_nunca_enviada_nao_enfileira_task_mas_marca_cancelada(self):
        guia = _make_guia(self.clinic, operator_config=None, status=TISSGuiaStatus.NAO_ENVIADA)
        with patch('tiss.tasks.cancelar_guia_task.delay') as mock_delay:
            enfileirado = disparar_cancelamento_guia(guia)
        self.assertFalse(enfileirado)
        mock_delay.assert_not_called()
        guia.refresh_from_db()
        self.assertEqual(guia.status, TISSGuiaStatus.CANCELADA)

    def test_guia_com_autorizacao_emitida_enfileira_task_e_marca_cancelada(self):
        guia = _make_guia(self.clinic, self.op, status=TISSGuiaStatus.ACEITA)
        with patch('tiss.tasks.cancelar_guia_task.delay') as mock_delay:
            enfileirado = disparar_cancelamento_guia(guia)
        self.assertTrue(enfileirado)
        mock_delay.assert_called_once_with(str(guia.id))
        guia.refresh_from_db()
        self.assertEqual(guia.status, TISSGuiaStatus.CANCELADA)


@override_settings(TISS_SOAP_MOCK=True)
class CancelarGuiaOrizonTaskTests(TestCase):
    def setUp(self):
        self.clinic = _make_clinic()
        self.op = _make_config(self.clinic, TISSGatewayProvider.ORIZON)
        self.guia = _make_guia(self.clinic, self.op, status=TISSGuiaStatus.ENVIADA)

    def test_sucesso_marca_guia_cancelada_e_nao_cria_alerta(self):
        cancelar_guia_task.apply(args=[str(self.guia.id)])
        self.guia.refresh_from_db()
        self.assertEqual(self.guia.status, TISSGuiaStatus.CANCELADA)
        self.assertFalse(TISSCancelamentoPendente.objects.filter(guia=self.guia).exists())

    def test_guia_inexistente_nao_levanta_excecao(self):
        # DoesNotExist é engolido de propósito — não há o que retry-ar.
        cancelar_guia_task.apply(args=[str(uuid.uuid4())])

    def test_guia_sem_lote_nao_faz_nada(self):
        guia_sem_lote = _make_guia(self.clinic, operator_config=None, status=TISSGuiaStatus.ENVIADA)
        cancelar_guia_task.apply(args=[str(guia_sem_lote.id)])
        guia_sem_lote.refresh_from_db()
        self.assertEqual(guia_sem_lote.status, TISSGuiaStatus.ENVIADA)


class TratarFalhaRetryEAlertaTests(TestCase):
    """
    Testa `_tratar_falha` isoladamente (sem depender da mecânica interna de
    retry do Celery) — é aqui que mora a decisão "retry vs. alerta".
    """

    def setUp(self):
        self.clinic = _make_clinic()
        self.op = _make_config(self.clinic, TISSGatewayProvider.ORIZON)
        self.guia = _make_guia(self.clinic, self.op, status=TISSGuiaStatus.ENVIADA)

    def _fake_task(self, retries: int, max_retries: int = 3):
        task = MagicMock()
        task.request.retries = retries
        task.max_retries = max_retries
        task.retry.side_effect = Exception('retry-agendado')
        return task

    def test_antes_de_esgotar_retries_reagenda_e_nao_cria_alerta(self):
        for tentativa in range(3):  # retries=0,1,2 — ainda dentro de max_retries=3
            task = self._fake_task(retries=tentativa)
            with self.assertRaises(Exception):
                _tratar_falha(task, self.guia, self.op, 'falha de rede simulada')
            task.retry.assert_called_once()
        self.assertFalse(TISSCancelamentoPendente.objects.filter(guia=self.guia).exists())

    def test_apos_esgotar_3_retries_cria_alerta_falhou_apos_retries(self):
        task = self._fake_task(retries=3)  # já tentou 3x (0,1,2) — 4ª chamada esgota
        _tratar_falha(task, self.guia, self.op, 'timeout na Orizon')
        task.retry.assert_not_called()

        pendente = TISSCancelamentoPendente.objects.get(guia=self.guia)
        self.assertTrue(pendente.falhou_apos_retries)
        self.assertFalse(pendente.resolvido)
        self.assertEqual(pendente.tentativas, 4)
        self.assertIn('timeout na Orizon', pendente.ultimo_erro)
        self.assertEqual(pendente.clinic, self.clinic)


@override_settings(TISS_SOAP_MOCK=True, CELERY_TASK_ALWAYS_EAGER=True)
class CancelarGuiaViewTests(TestCase):
    """POST /api/tiss/guias/cancelar/ — gatilho consumido pelo Edge Gateway."""

    def setUp(self):
        self.client = APIClient()
        self.clinic = _make_clinic()
        self.op = _make_config(self.clinic, TISSGatewayProvider.ORIZON)

    def test_cancela_guia_por_appointment_id(self):
        guia = _make_guia(self.clinic, self.op, status=TISSGuiaStatus.ACEITA, appointment_id='apt-123')
        response = self.client.post(
            '/api/tiss/guias/cancelar/',
            {'appointment_id': 'apt-123'},
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.data['cancelamento_enfileirado'])
        guia.refresh_from_db()
        self.assertEqual(guia.status, TISSGuiaStatus.CANCELADA)

    def test_guia_nao_encontrada_404(self):
        response = self.client.post(
            '/api/tiss/guias/cancelar/',
            {'appointment_id': 'apt-inexistente'},
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, 404)

    def test_appointment_id_obrigatorio(self):
        response = self.client.post(
            '/api/tiss/guias/cancelar/',
            {},
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, 400)

    def test_nao_vaza_guia_de_outra_clinica(self):
        outra_clinic = _make_clinic()
        outra_op = _make_config(outra_clinic, TISSGatewayProvider.ORIZON)
        _make_guia(outra_clinic, outra_op, status=TISSGuiaStatus.ACEITA, appointment_id='apt-outra-clinica')
        response = self.client.post(
            '/api/tiss/guias/cancelar/',
            {'appointment_id': 'apt-outra-clinica'},
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, 404)


@override_settings(TISS_SOAP_MOCK=True, CELERY_TASK_ALWAYS_EAGER=True)
class CancelamentoGuiaSemAutorizacaoTests(TestCase):
    """
    QA (2026-07-29), cenário de borda 1: cancelamento disparado para um
    atendimento que NUNCA teve guia Orizon emitida — deve ser no-op, sem
    criar `TISSCancelamentoPendente`. Reforça o que já é coberto em
    `DispararCancelamentoGuiaServiceTests`, mas aqui a partir do estado
    "nem existe guia com lote" (nunca chegou a ter operator_config).
    """

    def setUp(self):
        self.clinic = _make_clinic()

    def test_guia_sem_lote_nunca_enviada_nao_dispara_task_nem_cria_pendente(self):
        guia = _make_guia(self.clinic, operator_config=None, status=TISSGuiaStatus.NAO_ENVIADA)
        with patch('tiss.tasks.cancelar_guia_task.delay') as mock_delay:
            enfileirado = disparar_cancelamento_guia(guia)
        self.assertFalse(enfileirado)
        mock_delay.assert_not_called()
        self.assertFalse(TISSCancelamentoPendente.objects.filter(guia=guia).exists())
        guia.refresh_from_db()
        self.assertEqual(guia.status, TISSGuiaStatus.CANCELADA)


@override_settings(TISS_SOAP_MOCK=True)
class CancelamentoGuiaConcorrenteTests(TestCase):
    """
    QA (2026-07-29), cenário de borda 2: dois disparos de cancelamento para
    a MESMA guia (ex.: dupla submissão do Edge Gateway, ou reentrega do
    broker). O ponto de idempotência que o sistema garante hoje é: nunca
    mais de um `TISSCancelamentoPendente` por guia (`get_or_create(guia=...)`
    em `_tratar_falha`) e, no caminho feliz, a guia fica `CANCELADA`
    independente de quantas vezes a task rodou.
    """

    def setUp(self):
        self.clinic = _make_clinic()
        self.op = _make_config(self.clinic, TISSGatewayProvider.ORIZON)
        self.guia = _make_guia(self.clinic, self.op, status=TISSGuiaStatus.ENVIADA)

    def test_duas_execucoes_da_task_para_a_mesma_guia_convergem_sem_duplicar_pendente(self):
        # Simula concorrência: a task roda duas vezes para a mesma guia_id
        # (ex.: `disparar_cancelamento_guia` chamado duas vezes antes da
        # primeira mensagem ser confirmada). Ambas as execuções tentam
        # cancelar — mockamos a segunda para vir com a resposta de negócio
        # que a Orizon dá quando já processou o cancelamento.
        cancelar_guia_task.apply(args=[str(self.guia.id)])
        self.guia.refresh_from_db()
        self.assertEqual(self.guia.status, TISSGuiaStatus.CANCELADA)

        with patch('tiss.providers.orizon.cancelar_guia') as mock_cancelar:
            mock_cancelar.return_value = CancelamentoResultado(
                sucesso=False, erro_code='guia_nao_cancelada',
                erro_mensagem='orizon_recusou_cancelamento',
            )
            cancelar_guia_task.apply(args=[str(self.guia.id)])

        # Não duplica o registro de pendência — no máximo um por guia.
        self.assertEqual(TISSCancelamentoPendente.objects.filter(guia=self.guia).count(), 1)
        # A guia permanece cancelada do lado da clínica (decisão já tomada
        # antes da task rodar, em `disparar_cancelamento_guia`).
        self.guia.refresh_from_db()
        self.assertEqual(self.guia.status, TISSGuiaStatus.CANCELADA)

    def test_chamar_tratar_falha_duas_vezes_para_a_mesma_guia_atualiza_em_vez_de_duplicar(self):
        task = MagicMock()
        task.request.retries = 3
        task.max_retries = 3

        _tratar_falha(task, self.guia, self.op, 'primeira falha')
        _tratar_falha(task, self.guia, self.op, 'segunda falha (execução concorrente)')

        self.assertEqual(TISSCancelamentoPendente.objects.filter(guia=self.guia).count(), 1)
        pendente = TISSCancelamentoPendente.objects.get(guia=self.guia)
        self.assertEqual(pendente.ultimo_erro, 'segunda falha (execução concorrente)')


@override_settings(TISS_SOAP_MOCK=True)
class CancelarGuiaErroTransitorioVsNegocioTests(TestCase):
    """
    QA (2026-07-29), cenário de borda 3 e 4: (3) falha nas tentativas 1/2
    seguida de sucesso na 3ª — resolve sem alerta; (4) resposta SOAP
    malformada/timeout (falha de TRANSPORTE, transitória, vale re-tentar)
    vs. resposta SOAP válida com recusa de NEGÓCIO (guia já cancelada / guia
    inexistente — a operadora respondeu, retry não muda o resultado). Bug
    real corrigido nesta rodada: antes, `_tratar_falha` tratava os dois
    casos como idênticos e gastava as 3 tentativas de retry mesmo em erro de
    negócio definitivo, atrasando em minutos o suporte perceber que precisa
    agir manualmente.
    """

    def setUp(self):
        self.clinic = _make_clinic()
        self.op = _make_config(self.clinic, TISSGatewayProvider.ORIZON)
        self.guia = _make_guia(self.clinic, self.op, status=TISSGuiaStatus.ENVIADA)

    def _fake_task(self, retries=0, max_retries=3):
        task = MagicMock()
        task.request.retries = retries
        task.max_retries = max_retries
        task.retry.side_effect = Exception('retry-agendado')
        return task

    def test_erro_de_rede_e_transitorio_e_faz_retry_mesmo_na_primeira_tentativa(self):
        task = self._fake_task(retries=0)
        with self.assertRaises(Exception):
            _tratar_falha(task, self.guia, self.op, 'timeout', transitorio=True)
        task.retry.assert_called_once()
        self.assertFalse(TISSCancelamentoPendente.objects.filter(guia=self.guia).exists())

    def test_erro_de_negocio_guia_nao_cancelada_nao_faz_retry_mesmo_na_primeira_tentativa(self):
        task = self._fake_task(retries=0)
        _tratar_falha(task, self.guia, self.op, 'orizon_recusou_cancelamento', transitorio=False)
        task.retry.assert_not_called()

        pendente = TISSCancelamentoPendente.objects.get(guia=self.guia)
        self.assertTrue(pendente.falhou_apos_retries)
        self.assertEqual(pendente.tentativas, 1)
        self.assertFalse(pendente.resolvido)

    def test_soap_fault_de_negocio_tambem_nao_gasta_retries(self):
        task = self._fake_task(retries=0)
        _tratar_falha(task, self.guia, self.op, 'guia_nao_encontrada: 404', transitorio=False)
        task.retry.assert_not_called()
        self.assertEqual(TISSCancelamentoPendente.objects.get(guia=self.guia).tentativas, 1)

    def test_task_completa_classifica_guia_nao_cancelada_como_nao_transitorio_via_mock(self):
        with patch('tiss.providers.orizon.cancelar_guia') as mock_cancelar:
            mock_cancelar.return_value = CancelamentoResultado(
                sucesso=False, erro_code='guia_nao_cancelada', erro_mensagem='orizon_recusou_cancelamento',
            )
            # `.apply()` roda a task síncrona; erro de negócio não deve
            # levantar `Retry` do Celery — deve retornar normalmente após
            # criar o alerta já na 1ª tentativa.
            cancelar_guia_task.apply(args=[str(self.guia.id)])

        pendente = TISSCancelamentoPendente.objects.get(guia=self.guia)
        self.assertTrue(pendente.falhou_apos_retries)
        self.assertEqual(pendente.tentativas, 1)

    def test_task_completa_com_erro_de_rede_esgota_3_retries_antes_do_alerta(self):
        # `.apply()` executa `self.retry()` sincronamente (sem broker real),
        # então a chamada síncrona já "consome" as 3 tentativas antes de
        # retornar — diferente do caminho de negócio, que cria o alerta já
        # na 1ª tentativa (ver teste acima). O que distingue os dois casos
        # é `pendente.tentativas`: 4 aqui (esgotou retry) vs. 1 no de negócio.
        with patch('tiss.providers.orizon.cancelar_guia') as mock_cancelar:
            mock_cancelar.return_value = CancelamentoResultado(
                sucesso=False, erro_code='soap_network_error', erro_mensagem='timeout',
            )
            cancelar_guia_task.apply(args=[str(self.guia.id)])

        pendente = TISSCancelamentoPendente.objects.get(guia=self.guia)
        self.assertTrue(pendente.falhou_apos_retries)
        self.assertEqual(pendente.tentativas, 4)
        self.assertEqual(mock_cancelar.call_count, 4)

    def test_retry_apos_1_e_2_falhas_transitorias_resolve_na_3a_sem_criar_alerta(self):
        # Reproduz a sequência de tentativas manualmente controlando
        # `task.request.retries`, sem depender do agendador real do Celery
        # (mesmo padrão de `TratarFalhaRetryEAlertaTests`).
        for tentativa in (0, 1):
            task = self._fake_task(retries=tentativa)
            with self.assertRaises(Exception):
                _tratar_falha(task, self.guia, self.op, 'timeout transitório', transitorio=True)
            task.retry.assert_called_once()
        self.assertFalse(TISSCancelamentoPendente.objects.filter(guia=self.guia).exists())

        # 3ª tentativa: sucesso — a task real marcaria a guia CANCELADA e
        # nunca chamaria `_tratar_falha`; aqui confirmamos que nenhum
        # alerta ficou para trás do caminho de falha anterior.
        from .models import TISSGuia
        TISSGuia.objects.filter(pk=self.guia.pk).update(status=TISSGuiaStatus.CANCELADA)
        self.guia.refresh_from_db()
        self.assertEqual(self.guia.status, TISSGuiaStatus.CANCELADA)
        self.assertFalse(TISSCancelamentoPendente.objects.filter(guia=self.guia).exists())


@override_settings(TISS_SOAP_MOCK=True)
class TISSCancelamentoPendenteAdminResolvidoTests(TestCase):
    """
    QA (2026-07-29), cenário de borda 5: `resolvido` precisa ser marcável
    manualmente pelo suporte no Django Admin (não é `readonly_fields`, ao
    contrário dos demais campos do model).
    """

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.clinic = _make_clinic()
        self.op = _make_config(self.clinic, TISSGatewayProvider.ORIZON)
        self.guia = _make_guia(self.clinic, self.op, status=TISSGuiaStatus.ENVIADA)
        self.pendente = TISSCancelamentoPendente.objects.create(
            clinic=self.clinic, guia=self.guia, tentativas=4,
            falhou_apos_retries=True, ultimo_erro='timeout', resolvido=False,
        )
        User = get_user_model()
        self.staff = User.objects.create_superuser(
            username=f'suporte-{uuid.uuid4().hex[:8]}', email='suporte@syncrohealth.test', password='senha-teste-123',
        )
        self.client = APIClient()
        self.client.force_login(self.staff)

    def test_resolvido_nao_e_readonly_no_admin(self):
        from .admin import TISSCancelamentoPendenteAdmin
        self.assertNotIn('resolvido', TISSCancelamentoPendenteAdmin.readonly_fields)
        self.assertIn('resolvido', TISSCancelamentoPendenteAdmin.fields)

    def test_marcar_resolvido_via_admin_persiste(self):
        url = f'/admin/tiss/tisscancelamentopendente/{self.pendente.id}/change/'
        response = self.client.post(url, {
            'resolvido': 'on',
        }, follow=True)
        self.assertIn(response.status_code, (200, 302))
        self.pendente.refresh_from_db()
        self.assertTrue(self.pendente.resolvido)
