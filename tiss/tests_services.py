import uuid
from decimal import Decimal
from django.test import TestCase, override_settings

from clinics.models import Clinic
from .models import TISSOperatorConfig, TISSLote, TISSGuia, TISSLoteStatus, TISSGuiaStatus, TISSGlosa
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
