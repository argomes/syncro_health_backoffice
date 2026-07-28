import uuid
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from clinics.models import Clinic
from .models import (
    TISSOperatorConfig, TISSLote, TISSGuia, TISSLoteStatus, TISSGuiaStatus, TISSGlosa,
    TISSGatewayProvider,
)
from .services import criar_lote, enviar_lote, TISSServiceError


def _make_clinic(slug):
    return Clinic.objects.create(
        name=f'Clínica {slug}', slug=slug, cnpj=f'{uuid.uuid4().int % 10**14:014d}',
        db_name=f'db_{uuid.uuid4().hex[:8]}', db_user=f'u_{uuid.uuid4().hex[:8]}',
    )


@override_settings(TISS_SOAP_MOCK=True)
class EnviarLoteServiceTests(TestCase):
    def setUp(self):
        self.clinic = _make_clinic('servico-envio-teste')
        self.op = TISSOperatorConfig.objects.create(
            clinic=self.clinic, nome_operadora='Orizon', registro_ans='123456',
            endpoint_url='https://tiss-documentos.orizon.com.br/Service.asmx',
        )
        self.guia = TISSGuia.objects.create(
            clinic=self.clinic, numero='1', competencia='2026-07', numero_carteira='123', valor=Decimal('150.50'),
            procedimentos=[{'codigo': '10101012', 'descricao': 'Consulta', 'valor': 150.5, 'quantidade': 1}],
        )
        self.lote = criar_lote(self.clinic, self.op, '2026-07')
        self.guia.lote = self.lote
        self.guia.save()

    def test_envio_com_sucesso_atualiza_lote_e_guia(self):
        lote = enviar_lote(self.lote, mock_scenario='success')
        lote.refresh_from_db()
        self.assertEqual(lote.status, TISSLoteStatus.ENVIADO)
        self.assertEqual(lote.protocolo, 'MOCK-PROTO-000001')
        self.guia.refresh_from_db()
        self.assertEqual(self.guia.status, TISSGuiaStatus.ENVIADA)

    def test_envio_com_erro_cria_glosa_e_marca_lote_com_erro(self):
        with self.assertRaises(TISSServiceError):
            enviar_lote(self.lote, mock_scenario='error')
        self.lote.refresh_from_db()
        self.assertEqual(self.lote.status, TISSLoteStatus.ERRO_ENVIO)
        self.guia.refresh_from_db()
        self.assertEqual(self.guia.status, TISSGuiaStatus.GLOSADA)
        self.assertEqual(TISSGlosa.objects.filter(guia=self.guia).count(), 1)

    def test_lote_sem_guias_levanta_erro_de_servico(self):
        lote_vazio = criar_lote(self.clinic, self.op, '2026-08')
        with self.assertRaises(TISSServiceError):
            enviar_lote(lote_vazio, mock_scenario='success')


@override_settings(TISS_SOAP_MOCK=True)
class EnviarLoteOperadoraDesativadaTests(TestCase):
    """
    BACFF-AVULSA-12 (issue #45): `TISSOperatorConfig.ativo` precisa ser
    checado ANTES de qualquer I/O de rede com a operadora. Confirma que
    (a) operadora ativa segue funcionando sem regressão, (b) operadora
    inativa é bloqueada e o client SOAP NUNCA é sequer invocado.
    """

    def setUp(self):
        self.clinic = _make_clinic('servico-envio-desativada')
        self.op = TISSOperatorConfig.objects.create(
            clinic=self.clinic, nome_operadora='Genérica', registro_ans='654321',
            endpoint_url='https://tiss.exemplo.com.br/Service.asmx',
        )
        self.guia = TISSGuia.objects.create(
            clinic=self.clinic, numero='1', competencia='2026-07', numero_carteira='321', valor=Decimal('100.00'),
            procedimentos=[{'codigo': '10101012', 'descricao': 'Consulta', 'valor': 100.0, 'quantidade': 1}],
        )
        self.lote = criar_lote(self.clinic, self.op, '2026-07')
        self.guia.lote = self.lote
        self.guia.save()

    def test_operadora_ativa_prossegue_sem_regressao(self):
        lote = enviar_lote(self.lote, mock_scenario='success')
        lote.refresh_from_db()
        self.assertEqual(lote.status, TISSLoteStatus.ENVIADO)

    @patch('tiss.services.soap_enviar_lote')
    def test_operadora_inativa_bloqueia_antes_de_qualquer_io_de_rede(self, mock_soap_enviar_lote):
        self.op.ativo = False
        self.op.save(update_fields=['ativo'])

        with self.assertRaises(TISSServiceError) as ctx:
            enviar_lote(self.lote, mock_scenario='success')

        self.assertEqual(ctx.exception.code, 'operadora_desativada')
        mock_soap_enviar_lote.assert_not_called()
        self.lote.refresh_from_db()
        # Nada de XML/hash/status de envio deve ter sido tocado — o bloqueio
        # acontece antes de qualquer processamento do lote.
        self.assertEqual(self.lote.status, TISSLoteStatus.MONTANDO)


@override_settings(TISS_SOAP_MOCK=True)
class EnviarLoteProviderDispatchTests(TestCase):
    """
    BACFF-AVULSA-13 (issue #46): `enviar_lote` deve resolver o provider por
    `gateway_provider`, igual `consultar_elegibilidade_automatica` já faz —
    nunca chamar o client genérico incondicionalmente.
    """

    def setUp(self):
        self.clinic = _make_clinic('servico-envio-provider')
        self.guia_procedimentos = [{'codigo': '10101012', 'descricao': 'Consulta', 'valor': 100.0, 'quantidade': 1}]

    def _make_lote(self, gateway_provider):
        op = TISSOperatorConfig.objects.create(
            clinic=self.clinic, nome_operadora='Operadora', registro_ans=str(uuid.uuid4().int % 900000 + 100000),
            endpoint_url='https://tiss.exemplo.com.br/Service.asmx', gateway_provider=gateway_provider,
        )
        guia = TISSGuia.objects.create(
            clinic=self.clinic, numero='1', competencia='2026-07', numero_carteira='999', valor=Decimal('100.00'),
            procedimentos=self.guia_procedimentos,
        )
        lote = criar_lote(self.clinic, op, '2026-07')
        guia.lote = lote
        guia.save()
        return lote

    @patch('tiss.services.soap_enviar_lote')
    def test_clinica_orizon_nao_chama_client_generico(self, mock_soap_enviar_lote):
        lote = self._make_lote(TISSGatewayProvider.ORIZON)

        with self.assertRaises(TISSServiceError) as ctx:
            enviar_lote(lote, mock_scenario='success')

        self.assertEqual(ctx.exception.code, 'provider_lote_nao_implementado')
        mock_soap_enviar_lote.assert_not_called()
        lote.refresh_from_db()
        self.assertEqual(lote.status, TISSLoteStatus.ERRO_ENVIO)

    def test_clinica_generico_ans_preserva_comportamento_atual(self):
        lote = self._make_lote(TISSGatewayProvider.GENERICO_ANS)

        resultado = enviar_lote(lote, mock_scenario='success')

        self.assertEqual(resultado.status, TISSLoteStatus.ENVIADO)
