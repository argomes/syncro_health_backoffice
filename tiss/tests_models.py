import uuid
from decimal import Decimal
from django.test import TestCase

from clinics.models import Clinic
from .models import TISSOperatorConfig, TISSLote, TISSGuia, TISSGlosa, TISSLoteStatus
from .crypto import decrypt_credential


def _make_clinic(slug):
    return Clinic.objects.create(
        name=f'Clínica {slug}', slug=slug, cnpj=f'{uuid.uuid4().hex[:14]}',
        db_name=f'db_{uuid.uuid4().hex[:8]}', db_user=f'u_{uuid.uuid4().hex[:8]}',
    )


class TISSOperatorConfigModelTests(TestCase):
    def test_credenciais_nunca_ficam_em_texto_plano_no_banco(self):
        clinic = _make_clinic('op-cred-teste')
        op = TISSOperatorConfig(
            clinic=clinic, nome_operadora='Orizon', registro_ans='123456',
            endpoint_url='https://tiss-documentos.orizon.com.br/Service.asmx',
        )
        op.set_login('minha-senha-secreta-login')
        op.set_senha('minha-senha-secreta-senha')
        op.save()

        # Recarrega do banco puro (evita ler do cache do objeto Python).
        # BACFF — credencial mora em TISSOperatorConnection, não mais em
        # TISSOperatorConfig (ver `.claude/tasks/TISS-MULTI-OPERATOR-STRATEGY.md` §2).
        from django.db import connection as db_connection
        with db_connection.cursor() as cursor:
            cursor.execute(
                'SELECT login_encrypted, senha_encrypted FROM tiss_tissoperatorconnection WHERE id = %s',
                [op.connection.id.hex],
            )
            login_encrypted, senha_encrypted = cursor.fetchone()
        self.assertNotEqual(login_encrypted, 'minha-senha-secreta-login')
        self.assertNotEqual(senha_encrypted, 'minha-senha-secreta-senha')
        self.assertNotIn('minha-senha-secreta', login_encrypted)
        self.assertNotIn('minha-senha-secreta', senha_encrypted)

        # Mas decripta corretamente de volta.
        self.assertEqual(op.login_plain, 'minha-senha-secreta-login')
        self.assertEqual(op.senha_plain, 'minha-senha-secreta-senha')
        self.assertEqual(decrypt_credential(op.login_encrypted), 'minha-senha-secreta-login')

    def test_str_nunca_expoe_credenciais(self):
        clinic = _make_clinic('op-str-teste')
        op = TISSOperatorConfig(
            clinic=clinic, nome_operadora='Orizon', registro_ans='123456',
            endpoint_url='https://tiss-documentos.orizon.com.br/Service.asmx',
        )
        op.set_login('login-secreto')
        op.set_senha('senha-secreta')
        op.save()
        texto = str(op)
        self.assertNotIn('login-secreto', texto)
        self.assertNotIn('senha-secreta', texto)


class TISSLoteSequencialTests(TestCase):
    def test_numero_lote_sequencial_por_clinica_e_operadora(self):
        clinic = _make_clinic('lote-seq-teste')
        op = TISSOperatorConfig.objects.create(
            clinic=clinic, nome_operadora='Orizon', registro_ans='123456',
            endpoint_url='https://tiss-documentos.orizon.com.br/Service.asmx',
        )
        self.assertEqual(TISSLote.next_numero_lote(clinic, op), 1)
        TISSLote.objects.create(clinic=clinic, operator_config=op, numero_lote=1, competencia='2026-07')
        self.assertEqual(TISSLote.next_numero_lote(clinic, op), 2)

    def test_numero_lote_nao_e_compartilhado_entre_operadoras(self):
        clinic = _make_clinic('lote-seq-op-teste')
        op1 = TISSOperatorConfig.objects.create(
            clinic=clinic, nome_operadora='Orizon', registro_ans='111111', endpoint_url='https://a',
        )
        op2 = TISSOperatorConfig.objects.create(
            clinic=clinic, nome_operadora='Amil', registro_ans='222222', endpoint_url='https://b',
        )
        TISSLote.objects.create(clinic=clinic, operator_config=op1, numero_lote=1, competencia='2026-07')
        # op2 começa do 1 mesmo já existindo lote 1 para op1 na mesma clínica.
        self.assertEqual(TISSLote.next_numero_lote(clinic, op2), 1)


class TISSIsolationTests(TestCase):
    """Clínica A nunca deve enxergar dados de configuração/lote/guia/glosa da Clínica B."""

    def setUp(self):
        self.clinic_a = _make_clinic('isolamento-a')
        self.clinic_b = _make_clinic('isolamento-b')
        self.op_a = TISSOperatorConfig.objects.create(
            clinic=self.clinic_a, nome_operadora='Orizon', registro_ans='333333', endpoint_url='https://a',
        )
        self.op_b = TISSOperatorConfig.objects.create(
            clinic=self.clinic_b, nome_operadora='Orizon', registro_ans='444444', endpoint_url='https://b',
        )
        self.guia_a = TISSGuia.objects.create(clinic=self.clinic_a, numero='A1', competencia='2026-07', valor=Decimal('10.00'))
        self.guia_b = TISSGuia.objects.create(clinic=self.clinic_b, numero='B1', competencia='2026-07', valor=Decimal('20.00'))

    def test_operator_config_isolado_por_clinica(self):
        configs_a = TISSOperatorConfig.objects.filter(clinic=self.clinic_a)
        self.assertIn(self.op_a, configs_a)
        self.assertNotIn(self.op_b, configs_a)

    def test_guia_isolada_por_clinica(self):
        guias_a = TISSGuia.objects.filter(clinic=self.clinic_a)
        self.assertIn(self.guia_a, guias_a)
        self.assertNotIn(self.guia_b, guias_a)

    def test_glosa_de_uma_clinica_nao_aparece_para_outra(self):
        glosa_a = TISSGlosa.objects.create(guia=self.guia_a, codigo='0052', valor_glosado=Decimal('5.00'))
        TISSGlosa.objects.create(guia=self.guia_b, codigo='0052', valor_glosado=Decimal('7.00'))
        glosas_da_clinica_a = TISSGlosa.objects.filter(guia__clinic=self.clinic_a)
        self.assertIn(glosa_a, glosas_da_clinica_a)
        self.assertEqual(glosas_da_clinica_a.count(), 1)


class TISSOperatorConnectionTests(TestCase):
    """
    BACFF — correção do defeito de credencial duplicada descrito em
    `.claude/tasks/TISS-MULTI-OPERATOR-STRATEGY.md` §2: N operadoras reais
    atrás do mesmo agregador (ex.: Orizon) para a MESMA clínica devem
    compartilhar UMA `TISSOperatorConnection`, nunca uma credencial por
    operadora.
    """

    def test_duas_configs_mesmo_transporte_compartilham_a_mesma_connection(self):
        clinic = _make_clinic('conexao-compartilhada')
        bradesco = TISSOperatorConfig.objects.create(
            clinic=clinic, nome_operadora='Bradesco', registro_ans='005711',
            endpoint_url='https://wsp.orizonbrasil.com.br:6213/tiss/v40100/tissSolicitacaoProcedimento',
            gateway_provider='orizon',
        )
        bradesco.set_login('login-orizon-clinica-x')
        bradesco.set_senha('senha-orizon-clinica-x')

        cassi = TISSOperatorConfig.objects.create(
            clinic=clinic, nome_operadora='Cassi', registro_ans='300700',
            endpoint_url='https://wsp.orizonbrasil.com.br:6213/tiss/v40100/tissSolicitacaoProcedimento',
            gateway_provider='orizon',
        )

        self.assertEqual(bradesco.connection_id, cassi.connection_id)
        # A credencial setada via UMA config (Bradesco) já vale para a outra
        # (Cassi) — é exatamente o "uma rotação, uma escrita" do documento.
        self.assertEqual(cassi.connection.login_plain, 'login-orizon-clinica-x')
        self.assertEqual(cassi.connection.senha_plain, 'senha-orizon-clinica-x')

    def test_transporte_diferente_na_mesma_clinica_nao_compartilha_connection(self):
        clinic = _make_clinic('conexao-nao-compartilhada')
        via_orizon = TISSOperatorConfig.objects.create(
            clinic=clinic, nome_operadora='Bradesco', registro_ans='005711',
            endpoint_url='https://wsp.orizonbrasil.com.br:6213/tiss/v40100/tissSolicitacaoProcedimento',
            gateway_provider='orizon',
        )
        via_direto = TISSOperatorConfig.objects.create(
            clinic=clinic, nome_operadora='Amil', registro_ans='326305',
            endpoint_url='https://webservices.amil.com.br/tiss/',
            gateway_provider='generico_ans',
        )
        self.assertNotEqual(via_orizon.connection_id, via_direto.connection_id)

    def test_mesmo_endpoint_e_transporte_em_clinicas_diferentes_nao_compartilha_connection(self):
        """Isolamento multi-tenant: connection nunca é reaproveitada entre clínicas."""
        clinic_a = _make_clinic('conexao-tenant-a')
        clinic_b = _make_clinic('conexao-tenant-b')
        op_a = TISSOperatorConfig.objects.create(
            clinic=clinic_a, nome_operadora='Bradesco', registro_ans='005711',
            endpoint_url='https://wsp.orizonbrasil.com.br:6213/tiss/v40100/tissSolicitacaoProcedimento',
            gateway_provider='orizon',
        )
        op_b = TISSOperatorConfig.objects.create(
            clinic=clinic_b, nome_operadora='Bradesco', registro_ans='005711',
            endpoint_url='https://wsp.orizonbrasil.com.br:6213/tiss/v40100/tissSolicitacaoProcedimento',
            gateway_provider='orizon',
        )
        self.assertNotEqual(op_a.connection_id, op_b.connection_id)
        self.assertEqual(op_a.connection.clinic_id, clinic_a.id)
        self.assertEqual(op_b.connection.clinic_id, clinic_b.id)
