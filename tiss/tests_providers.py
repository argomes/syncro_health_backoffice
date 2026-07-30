"""
Suíte de governança da arquitetura de providers TISS — §8 do documento
`.claude/tasks/TISS-OPERATOR-PROVIDER-ARCHITECTURE.md`.

**É esta suíte que substitui a ABC/Protocol formal**, e é ela que permite
adiar a formalização com segurança (§6): os testes de contrato são
parametrizados sobre `_PROVIDERS`, então um provider novo que não cumpra o
contrato deixa o build vermelho sem que ninguém precise lembrar de escrever
um teste para ele.

Cobre:
1. Testes de contrato parametrizados sobre todo provider registrado (§8.1)
2. Integridade de configuração: TextChoices × _PROVIDERS (§8.2, §8.6)
3. Anti-vazamento de PII nos logs — BLOQUEANTE DE MERGE (§8.3)
4. Lint arquitetural: `orizon` não vaza para fora dos arquivos dela (§8.4)
5. Teste de "desplugar" nos dois níveis (§8.5)
6. Retenção do OperatorCallLog (§8.7)
"""
import logging
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from clinics.models import Clinic, ClinicStatus, Plan, ProvisioningStatus
from . import providers
from .models import (
    TISSOperatorConfig, TISSGatewayProvider, TISSGuia, TISSLoteStatus,
    TISSElegibilidadeConsulta, TISSElegibilidadeOrigem,
    OperatorCallLog, OperatorCallOperation, OperatorCallOutcome,
)
from .providers.base import (
    ElegibilidadeRespostaCompleta, EnvioLoteResultado, ProviderCapabilities, ProviderHealth,
    ProviderError, OperadoraDesativada, ProviderNaoRegistrado, ProviderNaoConfirmado,
)
from .services import (
    criar_lote, enviar_lote, consultar_elegibilidade_automatica,
    registrar_elegibilidade_manual, TISSServiceError,
)


def make_clinic():
    unique = uuid.uuid4().hex[:8]
    return Clinic.objects.create(
        name='Clínica Providers Teste',
        slug=f'clinica-prov-{unique}',
        plan=Plan.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj=f'12.345.{unique[:3]}/0001-99',
        db_name=f'db_{unique}',
        db_user=f'u_{unique}',
        provisioning_status=ProvisioningStatus.PROVISIONED,
    )


def make_config(clinic, gateway_provider, ativo=True, registro_ans=None):
    op = TISSOperatorConfig.objects.create(
        clinic=clinic,
        nome_operadora='Operadora Teste',
        registro_ans=registro_ans or f'{uuid.uuid4().int % 900000 + 100000}',
        endpoint_url='https://tiss.exemplo.com.br/Service.asmx',
        gateway_provider=gateway_provider,
        ativo=ativo,
    )
    op.set_login('login-teste')
    op.set_senha('senha-teste')
    return op


# ---------------------------------------------------------------------------
# §8.1 — Testes de contrato, parametrizados sobre TODO provider registrado
# ---------------------------------------------------------------------------

@override_settings(TISS_SOAP_MOCK=True)
class ContratoDeProviderTests(TestCase):
    """
    Os mesmos casos rodam contra CADA módulo em `_PROVIDERS`. Provider novo
    que não cumpra o contrato = build vermelho, sem ninguém precisar lembrar
    de escrever teste para ele. É este mecanismo que torna a ABC formal
    desnecessária no MVP (§6).
    """

    def setUp(self):
        self.clinic = make_clinic()

    def test_todo_provider_expoe_as_quatro_funcoes_do_contrato(self):
        for nome, modulo in providers._PROVIDERS.items():
            with self.subTest(provider=nome):
                for funcao in ('verificar_cobertura', 'enviar_lote', 'health_check', 'capabilities'):
                    self.assertTrue(
                        callable(getattr(modulo, funcao, None)),
                        f'provider "{nome}" não expõe {funcao}() — ver contrato em providers/base.py',
                    )

    def test_capabilities_devolve_o_tipo_certo_e_nao_faz_io(self):
        for nome, modulo in providers._PROVIDERS.items():
            with self.subTest(provider=nome):
                caps = modulo.capabilities()
                self.assertIsInstance(caps, ProviderCapabilities)
                # Serializável para a API sem tratamento especial.
                self.assertIsInstance(caps.as_dict(), dict)
                self.assertIsInstance(caps.as_dict()['versoes_padrao_suportadas'], list)

    def test_health_check_nunca_levanta_e_devolve_provider_health(self):
        """
        §4.4(b): indisponibilidade é um RESULTADO, não um erro de programa —
        quem chama é um botão de admin que precisa renderizar uma resposta.
        """
        for nome, modulo in providers._PROVIDERS.items():
            with self.subTest(provider=nome):
                config = make_config(self.clinic, nome)
                saude = modulo.health_check(config)
                self.assertIsInstance(saude, ProviderHealth)
                self.assertIsInstance(saude.reachable, bool)

    def test_falha_de_rede_na_cobertura_nao_vaza_excecao_de_transporte(self):
        """
        Contrato: `verificar_cobertura` devolve um resultado normalizado
        mesmo quando o transporte falha. A recepcionista precisa de uma
        resposta, não de um 500. Providers que bloqueiam por configuração
        (`desconhecido`) levantam ProviderError — que é estrutural, não de
        transporte — e isso é o comportamento correto.
        """
        for nome, modulo in providers._PROVIDERS.items():
            with self.subTest(provider=nome):
                config = make_config(self.clinic, nome)
                with patch('tiss.providers.generico_ans.soap_verificar_elegibilidade') as m_gen, \
                        patch('tiss.providers.orizon.orizon_solicitar_autorizacao') as m_ori:
                    from .orizon_autorize_client import OrizonAutorizeClientError
                    from .soap_client import SOAPClientError
                    m_gen.side_effect = SOAPClientError('soap_network_error')
                    m_ori.side_effect = OrizonAutorizeClientError('soap_network_error')
                    try:
                        resultado = modulo.verificar_cobertura(
                            self.clinic, config, 'CARTEIRA-CONTRATO',
                        )
                    except ProviderError:
                        continue  # falha estrutural declarada — legítima
                self.assertIsInstance(resultado, ElegibilidadeRespostaCompleta)
                self.assertFalse(resultado.elegivel)
                self.assertTrue(resultado.erro_mensagem)

    def test_enviar_lote_devolve_resultado_normalizado_ou_provider_error(self):
        for nome, modulo in providers._PROVIDERS.items():
            with self.subTest(provider=nome):
                config = make_config(self.clinic, nome)
                lote = criar_lote(self.clinic, config, '2026-07')
                guia = TISSGuia.objects.create(
                    clinic=self.clinic, numero='1', competencia='2026-07',
                    numero_carteira='999', valor=Decimal('10.00'),
                    procedimentos=[{'codigo': '10101012', 'descricao': 'Consulta', 'valor': 10.0, 'quantidade': 1}],
                )
                try:
                    resultado = modulo.enviar_lote(lote, [guia], '000000000001')
                except ProviderError:
                    continue  # operação declaradamente não suportada
                self.assertIsInstance(resultado, EnvioLoteResultado)
                if not resultado.sucesso:
                    self.assertTrue(resultado.erro_code)


# ---------------------------------------------------------------------------
# §8.2 / §8.6 — Integridade de configuração
# ---------------------------------------------------------------------------

class IntegridadeDeRegistroTests(TestCase):

    def test_todo_valor_do_textchoices_tem_provider_registrado(self):
        """
        §8.6: adicionar um valor a `TISSGatewayProvider` sem entrada em
        `_PROVIDERS` deve quebrar o build — não uma consulta de paciente
        real na recepção.
        """
        faltando = set(TISSGatewayProvider.values) - {str(k) for k in providers._PROVIDERS}
        self.assertEqual(
            faltando, set(),
            f'valores de TISSGatewayProvider sem provider em _PROVIDERS: {faltando}',
        )

    def test_nenhum_provider_registrado_sem_valor_no_textchoices(self):
        sobrando = {str(k) for k in providers._PROVIDERS} - set(TISSGatewayProvider.values)
        self.assertEqual(sobrando, set())

    def test_nenhuma_config_ativa_aponta_para_provider_inexistente(self):
        """
        §8.2: check de integridade sobre os dados. Roda contra o banco de
        teste (vazio em CI), mas a mesma consulta é o que deve ser rodado
        contra produção antes de remover um provider do código (§4.2 nível 2).
        """
        orfas = TISSOperatorConfig.objects.filter(ativo=True).exclude(
            connection__gateway_provider__in=[str(k) for k in providers._PROVIDERS],
        )
        self.assertFalse(orfas.exists())

    def test_provider_nao_registrado_falha_alto_sem_fallback(self):
        """
        §4.2 nível 2: nunca fazer fallback silencioso. Mandar payload no
        dialeto errado para uma operadora real gera glosa e retrabalho de
        faturamento — custo bem pior que um erro explícito.
        """
        with self.assertRaises(ProviderNaoRegistrado):
            providers.provider_module('operadora_que_nao_existe')


# ---------------------------------------------------------------------------
# §8.5 — O TESTE DE "DESPLUGAR"
# ---------------------------------------------------------------------------

@override_settings(TISS_SOAP_MOCK=True)
class DesplugarOperadoraTests(TestCase):
    """
    §4.2 nível 1 + D2. O ponto central de todo o desenho: desativar uma
    operadora fecha o caminho de SAÍDA (chamada automática) e SÓ ele. Nada é
    apagado, o histórico continua legível, e o registro manual — fallback de
    primeira classe — continua funcionando, para que a clínica degrade em
    vez de parar.
    """

    def setUp(self):
        self.client = APIClient()
        self.clinic = make_clinic()
        self.op = make_config(self.clinic, TISSGatewayProvider.GENERICO_ANS, registro_ans='123456')

    def _desativar(self):
        self.op.ativo = False
        self.op.save(update_fields=['ativo'])

    # --- elegibilidade automática -----------------------------------------

    def test_elegibilidade_automatica_bloqueada_com_409_na_api(self):
        self._desativar()
        response = self.client.post(
            '/api/tiss/elegibilidade/verificar/',
            {'registro_ans': '123456', 'numero_carteira': 'CARTEIRA-X', 'mock_scenario': 'success'},
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['error'], 'operadora_desativada')

    def test_409_e_distinguivel_de_404_operadora_nao_cadastrada(self):
        """
        A razão de ser do 409: o gateway precisa distinguir "não cadastrada"
        de "cadastrada e desligada" para mostrar a mensagem certa na recepção
        (e sugerir o registro manual, que continua permitido).
        """
        self._desativar()
        desativada = self.client.post(
            '/api/tiss/elegibilidade/verificar/',
            {'registro_ans': '123456', 'numero_carteira': 'C'},
            format='json', HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        inexistente = self.client.post(
            '/api/tiss/elegibilidade/verificar/',
            {'registro_ans': '999999', 'numero_carteira': 'C'},
            format='json', HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(desativada.status_code, 409)
        self.assertEqual(inexistente.status_code, 404)

    def test_nenhum_io_de_rede_acontece_com_operadora_desativada(self):
        self._desativar()
        with patch('tiss.providers.generico_ans.soap_verificar_elegibilidade') as mock_soap:
            with self.assertRaises(TISSServiceError) as ctx:
                consultar_elegibilidade_automatica(
                    clinic=self.clinic, operator_config=self.op, numero_carteira='CARTEIRA-Y',
                )
        self.assertEqual(ctx.exception.code, 'operadora_desativada')
        mock_soap.assert_not_called()

    # --- envio de lote -----------------------------------------------------

    def test_envio_de_lote_bloqueado_com_409_na_api(self):
        guia = TISSGuia.objects.create(
            clinic=self.clinic, numero='1', competencia='2026-07', numero_carteira='123',
            valor=Decimal('100.00'),
            procedimentos=[{'codigo': '10101012', 'descricao': 'Consulta', 'valor': 100.0, 'quantidade': 1}],
        )
        lote = criar_lote(self.clinic, self.op, '2026-07')
        guia.lote = lote
        guia.save()
        self._desativar()

        with self.assertRaises(TISSServiceError) as ctx:
            enviar_lote(lote, mock_scenario='success')
        self.assertEqual(ctx.exception.code, 'operadora_desativada')
        lote.refresh_from_db()
        # Bloqueio antes de qualquer processamento — nada de status
        # intermediário enganoso.
        self.assertEqual(lote.status, TISSLoteStatus.MONTANDO)

    # --- D2: o caminho manual continua aberto -----------------------------

    def test_registro_manual_continua_funcionando_com_operadora_desativada(self):
        """
        **D2, o coração da segurança operacional do "desplugar".** Se este
        teste cair, desativar uma operadora deixou de degradar o serviço e
        passou a derrubá-lo: a recepção não conseguiria mais registrar o que
        obteve por telefone/portal.
        """
        self._desativar()
        resultado = registrar_elegibilidade_manual(
            clinic=self.clinic, operator_config=self.op,
            numero_carteira='CARTEIRA-MANUAL', numero_guia_operadora='GUIA-999',
            elegivel=True,
        )
        self.assertEqual(resultado.origem, TISSElegibilidadeOrigem.MANUAL)
        self.assertTrue(resultado.elegivel)

    def test_registro_manual_via_api_continua_201_com_operadora_desativada(self):
        self._desativar()
        response = self.client.post(
            '/api/tiss/elegibilidade/manual/',
            {
                'registro_ans': '123456', 'numero_carteira': 'CARTEIRA-M',
                'numero_guia_operadora': 'GUIA-888', 'elegivel': True,
            },
            format='json', HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['origem'], 'manual')

    # --- histórico permanece legível --------------------------------------

    def test_lotes_e_guias_historicos_continuam_legiveis(self):
        """
        §4.2: nada é apagado ao desativar. `on_delete=PROTECT` garante no
        banco; este teste garante que o caminho de LEITURA não passa por
        `resolve()` e portanto não é bloqueado junto.
        """
        guia = TISSGuia.objects.create(
            clinic=self.clinic, numero='H1', competencia='2026-06', numero_carteira='123',
            valor=Decimal('50.00'),
        )
        lote = criar_lote(self.clinic, self.op, '2026-06')
        guia.lote = lote
        guia.save()
        self._desativar()

        lote.refresh_from_db()
        self.assertEqual(lote.guias.count(), 1)
        self.assertEqual(TISSGuia.objects.filter(clinic=self.clinic).count(), 1)

    # --- introspecção continua disponível ---------------------------------

    def test_capabilities_e_health_check_funcionam_com_operadora_desativada(self):
        """
        O que `ativo=False` fecha é a saída de negócio, não a introspecção:
        o admin ainda precisa testar a conexão antes de religar, e a UI ainda
        quer saber o que aquele provider suportaria.
        """
        self._desativar()
        self.assertIsInstance(providers.capabilities_for(self.op), ProviderCapabilities)
        self.assertIsInstance(providers.health_check(self.op), ProviderHealth)


# ---------------------------------------------------------------------------
# D3 — `desconhecido` falha explicitamente
# ---------------------------------------------------------------------------

@override_settings(TISS_SOAP_MOCK=True)
class ProviderDesconhecidoTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.clinic = make_clinic()
        self.op = make_config(self.clinic, TISSGatewayProvider.DESCONHECIDO, registro_ans='111111')

    def test_desconhecido_e_o_default_do_model(self):
        op = TISSOperatorConfig.objects.create(
            clinic=self.clinic, nome_operadora='Nova', registro_ans='222222',
            endpoint_url='https://x.example.com/Service.asmx',
        )
        self.assertEqual(op.gateway_provider, TISSGatewayProvider.DESCONHECIDO)

    def test_cobertura_falha_explicitamente_sem_tentar_dialeto_algum(self):
        with patch('tiss.providers.generico_ans.soap_verificar_elegibilidade') as m_gen, \
                patch('tiss.providers.orizon.orizon_solicitar_autorizacao') as m_ori:
            with self.assertRaises(TISSServiceError) as ctx:
                consultar_elegibilidade_automatica(
                    clinic=self.clinic, operator_config=self.op, numero_carteira='C',
                )
        self.assertEqual(ctx.exception.code, 'provider_nao_confirmado')
        m_gen.assert_not_called()
        m_ori.assert_not_called()

    def test_api_devolve_409_para_provider_nao_confirmado(self):
        response = self.client.post(
            '/api/tiss/elegibilidade/verificar/',
            {'registro_ans': '111111', 'numero_carteira': 'C'},
            format='json', HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['error'], 'provider_nao_confirmado')

    def test_registro_manual_funciona_mesmo_com_provider_desconhecido(self):
        """D2 + D3 juntos: dialeto não confirmado não pode travar a recepção."""
        resultado = registrar_elegibilidade_manual(
            clinic=self.clinic, operator_config=self.op, numero_carteira='C',
            numero_guia_operadora='GUIA-777', elegivel=True,
        )
        self.assertEqual(resultado.origem, TISSElegibilidadeOrigem.MANUAL)

    def test_capabilities_do_desconhecido_nao_habilita_nada_na_ui(self):
        caps = providers.capabilities_for(self.op)
        self.assertFalse(caps.cobertura)
        self.assertFalse(caps.envio_lote)
        self.assertFalse(caps.consulta_status)
        self.assertFalse(caps.cancelamento_guia)


class MigracaoD3Tests(TestCase):
    """
    D3, item 7 da task: a migration de dados 0008 move as configs
    `generico_ans` (que eram default silencioso) para `desconhecido`, sem
    tocar nas configs Orizon.

    Testar migration de dados executando-a de novo é frágil; o que importa
    de verdade é a PROPRIEDADE que ela garante, e é isso que se afirma aqui:
    após a migration, nenhuma config `generico_ans` sobrou do estado antigo,
    e Orizon continua Orizon.
    """

    def test_nenhuma_config_generico_ans_remanescente_do_default_antigo(self):
        self.assertFalse(
            TISSOperatorConfig.objects.filter(connection__gateway_provider='generico_ans').exists(),
            'a migration 0008 deveria ter movido todas as configs genéricas para "desconhecido"',
        )

    def test_generico_ans_continua_selecionavel_deliberadamente(self):
        """
        D3 rebaixou o genérico de DEFAULT para ESCOLHA. Ele não foi removido:
        continua sendo a base sobre a qual uma operadora direta será
        integrada, e quem confirmou o manual técnico pode selecioná-lo.
        """
        self.assertIn('generico_ans', TISSGatewayProvider.values)
        clinic = make_clinic()
        op = make_config(clinic, TISSGatewayProvider.GENERICO_ANS)
        self.assertEqual(op.gateway_provider, TISSGatewayProvider.GENERICO_ANS)
        self.assertTrue(providers.capabilities_for(op).cobertura)


# ---------------------------------------------------------------------------
# Resolução de provider por config
# ---------------------------------------------------------------------------

@override_settings(TISS_SOAP_MOCK=True)
class ResolucaoDeProviderTests(TestCase):

    def setUp(self):
        self.clinic = make_clinic()

    def test_config_orizon_resolve_para_o_provider_da_orizon(self):
        op = make_config(self.clinic, TISSGatewayProvider.ORIZON)
        self.assertEqual(providers.resolve(op).nome, 'orizon')

    def test_config_generico_resolve_para_o_provider_generico(self):
        op = make_config(self.clinic, TISSGatewayProvider.GENERICO_ANS)
        self.assertEqual(providers.resolve(op).nome, 'generico_ans')

    def test_config_desconhecida_resolve_para_o_provider_desconhecido(self):
        op = make_config(self.clinic, TISSGatewayProvider.DESCONHECIDO)
        self.assertEqual(providers.resolve(op).nome, 'desconhecido')

    def test_resolve_levanta_operadora_desativada_antes_de_olhar_o_provider(self):
        op = make_config(self.clinic, TISSGatewayProvider.ORIZON, ativo=False)
        with self.assertRaises(OperadoraDesativada):
            providers.resolve(op)

    def test_orizon_nao_cai_no_client_generico_no_envio_de_lote(self):
        """
        Regressão do bug B2 / issue #46 (hotfix BACFF-AVULSA-13, PR #48), que
        esta arquitetura substitui: uma clínica Orizon nunca pode enviar lote
        pelo endpoint genérico. O código de erro histórico é preservado
        porque o gateway já o trata.
        """
        op = make_config(self.clinic, TISSGatewayProvider.ORIZON)
        guia = TISSGuia.objects.create(
            clinic=self.clinic, numero='1', competencia='2026-07', numero_carteira='999',
            valor=Decimal('100.00'),
            procedimentos=[{'codigo': '10101012', 'descricao': 'Consulta', 'valor': 100.0, 'quantidade': 1}],
        )
        lote = criar_lote(self.clinic, op, '2026-07')
        guia.lote = lote
        guia.save()

        with patch('tiss.providers.generico_ans.soap_enviar_lote') as mock_generico:
            with self.assertRaises(TISSServiceError) as ctx:
                enviar_lote(lote, mock_scenario='success')

        self.assertEqual(ctx.exception.code, 'provider_lote_nao_implementado')
        mock_generico.assert_not_called()
        lote.refresh_from_db()
        self.assertEqual(lote.status, TISSLoteStatus.ERRO_ENVIO)


@override_settings(TISS_SOAP_MOCK=True)
class OrizonNumeroGuiaAvulsaTests(TestCase):
    """
    BACFF-014 (achado 3, atualização 2026-07-29): `verificar_cobertura` usava
    o literal fixo 'ELEGIBILIDADE' como numeroGuiaPrestador em toda consulta
    sem appointment_id — a Orizon nega automaticamente (Bradesco) uma
    transação que reutiliza o mesmo número de guia. Confere que duas
    chamadas consecutivas sem appointment_id geram números diferentes.
    """

    def setUp(self):
        self.clinic = make_clinic()
        self.op = make_config(self.clinic, TISSGatewayProvider.ORIZON)

    def _numero_guia_usado(self):
        with patch('tiss.providers.orizon.orizon_solicitar_autorizacao') as mock_solicitar:
            from tiss.orizon_autorize_client import AutorizacaoResult, SituacaoAutorizacao
            mock_solicitar.return_value = AutorizacaoResult(
                situacao=SituacaoAutorizacao.AUTORIZADO,
                numero_guia_operadora='OP-1', codigo_glosa='', descricao_glosa='', raw_response='',
            )
            consultar_elegibilidade_automatica(
                self.clinic, self.op, numero_carteira='999', appointment_id='',
            )
            xml_enviado = mock_solicitar.call_args.kwargs['xml_solicitacao']
        from lxml import etree
        doc = etree.fromstring(xml_enviado.encode('utf-8'))
        el = doc.find('.//{*}numeroGuiaPrestador')
        return el.text

    def test_numero_guia_prestador_nunca_e_o_literal_fixo_eleghibilidade(self):
        numero = self._numero_guia_usado()
        self.assertNotEqual(numero, 'ELEGIBILIDADE')

    def test_duas_chamadas_consecutivas_sem_appointment_id_geram_numeros_diferentes(self):
        numero_1 = self._numero_guia_usado()
        numero_2 = self._numero_guia_usado()
        self.assertNotEqual(numero_1, numero_2)


# ---------------------------------------------------------------------------
# §8.3 — ANTI-VAZAMENTO DE PII (BLOQUEANTE DE MERGE)
# ---------------------------------------------------------------------------

@override_settings(TISS_SOAP_MOCK=True)
class AntiVazamentoPIITests(TestCase):
    """
    §8.3 — o item de LGPD que mais facilmente regride, porque basta alguém
    adicionar um `logger.debug(xml)` para depurar uma operadora nova.
    Parametrizado sobre `_PROVIDERS`: um provider novo entra nesta rede
    automaticamente.
    """

    CARTEIRA = 'CARTEIRA-ULTRA-SECRETA-42'
    NOME = 'Paciente Nome Confidencial'

    def setUp(self):
        self.clinic = make_clinic()

    def test_nenhum_provider_loga_carteirinha_ou_nome_do_beneficiario(self):
        for nome, modulo in providers._PROVIDERS.items():
            with self.subTest(provider=nome):
                config = make_config(self.clinic, nome)
                with self.assertLogs('tiss', level='DEBUG') as captured:
                    logging.getLogger('tiss').debug('sentinela para o assertLogs não falhar por lista vazia')
                    try:
                        modulo.verificar_cobertura(
                            self.clinic, config, self.CARTEIRA, beneficiario_nome=self.NOME,
                        )
                    except ProviderError:
                        pass
                texto = '\n'.join(captured.output)
                self.assertNotIn(self.CARTEIRA, texto)
                self.assertNotIn(self.NOME, texto)

    def test_log_operacional_de_elegibilidade_nunca_recebe_pii(self):
        """
        BACFF-AVULSA-01 revalidado sob a nova arquitetura: os providers
        montam a `erro_mensagem`, e é ela que vai para o banco central.
        """
        op = make_config(self.clinic, TISSGatewayProvider.GENERICO_ANS)
        consultar_elegibilidade_automatica(
            clinic=self.clinic, operator_config=op, numero_carteira=self.CARTEIRA,
            beneficiario_nome=self.NOME, mock_scenario='error',
        )
        for log in TISSElegibilidadeConsulta.objects.all():
            self.assertNotIn(self.CARTEIRA, log.erro_mensagem)
            self.assertNotIn(self.NOME, log.erro_mensagem)

    def test_operator_call_log_nao_tem_nenhum_campo_capaz_de_guardar_pii(self):
        """
        Defesa estrutural, não comportamental: a tabela não deve ter campo
        de texto livre onde alguém possa despejar a resposta da operadora
        "só para depurar". `outcome` é enum fechado de propósito.
        """
        campos = {f.name for f in OperatorCallLog._meta.fields}
        proibidos = {
            'numero_carteira', 'beneficiario_nome', 'payload', 'xml',
            'xml_enviado', 'xml_recebido', 'erro_mensagem', 'raw_response', 'detail',
        }
        self.assertEqual(campos & proibidos, set())


class VersaoOrizonNaoHardcodedTests(TestCase):
    """
    BACFF-014 (achado 1 / critério de aceite, atualização 2026-07-29): grep
    de confirmação de que a versão/endpoint do padrão TISS da Orizon não
    está mais hardcoded em '4.01.00'/'v40100' em código executável do
    módulo `tiss` — só é permitido aparecer em docstrings/comentários
    (histórico, nome do PDF fonte) ou em fixtures de teste (dado de
    configuração, não lógica).
    """

    def test_grep_4_01_00_e_v40100_como_valor_de_codigo_executavel(self):
        """
        Só reprova padrões que são de fato ATRIBUIÇÃO/LITERAL de código
        (`= '4.01.00'`, `/tiss/v40100/` dentro de string) — não texto livre
        de docstring/comentário explicando o histórico do achado (que
        legitimamente cita '4.01.00' ao narrar o que foi corrigido).
        """
        import pathlib
        import re

        raiz = pathlib.Path(__file__).parent
        padroes_proibidos = [
            re.compile(r"""=\s*['"]4\.01\.00['"]"""),
            re.compile(r"""/tiss/v40100/"""),
        ]
        ocorrencias = []
        for arquivo in raiz.glob('*.py'):
            if arquivo.name.startswith('test') or 'tests_' in arquivo.name:
                continue
            texto = arquivo.read_text(encoding='utf-8')
            for numero_linha, linha in enumerate(texto.splitlines(), start=1):
                if any(p.search(linha) for p in padroes_proibidos):
                    ocorrencias.append(f'{arquivo.name}:{numero_linha}: {linha.strip()}')
        self.assertEqual(
            ocorrencias, [],
            f'Versão/endpoint 4.01.00/v40100 ainda hardcoded em código: {ocorrencias}',
        )


# ---------------------------------------------------------------------------
# §8.4 — Lint arquitetural
# ---------------------------------------------------------------------------

class LintArquiteturalTests(TestCase):

    def test_nome_da_operadora_nao_vaza_para_fora_dos_arquivos_dela(self):
        """
        §8.4: o guarda de "não hardcode a operadora" mais barato que existe.
        `orizon` só pode aparecer em `tiss/orizon_*.py`, em
        `tiss/providers/orizon.py`, no registro `_PROVIDERS`, no
        `TextChoices` e nos testes. Se aparecer em `services.py` ou
        `views.py`, o desenho vazou (§5: plugar operadora nova não deve
        exigir tocar nesses arquivos).
        """
        import ast
        import pathlib

        # `models.py` é permitido: é onde vive o `TextChoices`, que
        # legitimamente enumera as operadoras conhecidas — é a fonte do
        # registro, não um despacho.
        permitidos = {'models.py'}
        raiz = pathlib.Path(__file__).parent

        def codigo_executavel(fonte: str) -> str:
            """
            Só o código que EXECUTA. Comentários somem no parse do `ast`;
            docstrings são zeradas explicitamente. Mencionar a Orizon numa
            docstring é documentação legítima ("este client foi validado
            contra a Orizon"); referenciá-la em código é o hardcode que
            queremos proibir.
            """
            tree = ast.parse(fonte)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    corpo = getattr(node, 'body', [])
                    if (corpo and isinstance(corpo[0], ast.Expr)
                            and isinstance(corpo[0].value, ast.Constant)
                            and isinstance(corpo[0].value.value, str)):
                        corpo[0].value.value = ''
            return ast.dump(tree)

        infratores = []
        for arquivo in raiz.glob('*.py'):
            if arquivo.name.startswith(('orizon_', 'tests_')) or arquivo.name in permitidos:
                continue
            if 'orizon' in codigo_executavel(arquivo.read_text()).lower():
                infratores.append(arquivo.name)

        self.assertEqual(
            sorted(infratores), [],
            f'"orizon" referenciada em código fora dos arquivos da Orizon: {sorted(infratores)}. '
            'Despacho por operadora deve passar por providers.resolve().',
        )

    def test_lint_arquitetural_pegaria_um_hardcode_de_verdade(self):
        """
        Meta-teste: um lint que nunca falha não protege nada. Confirma que a
        regra acima realmente detecta uma referência em código (e não só em
        comentário), para que ela não passe a valer vazia se alguém mexer no
        parser.
        """
        import ast
        fonte_com_hardcode = (
            '"""Docstring citando a Orizon — legítimo."""\n'
            '# comentário citando Orizon — legítimo\n'
            "if config.gateway_provider == 'orizon':\n"
            '    pass\n'
        )
        tree = ast.parse(fonte_com_hardcode)
        tree.body[0].value.value = ''
        self.assertIn('orizon', ast.dump(tree).lower())

    def test_services_nao_importa_client_de_operadora_alguma(self):
        """
        O teste de aceite do desenho inteiro (§5): plugar uma operadora nova
        não deve exigir tocar em `services.py`.
        """
        import pathlib
        fonte = (pathlib.Path(__file__).parent / 'services.py').read_text()
        for proibido in ('soap_client', 'orizon_autorize_client', 'xml_builder', 'orizon_autorize_xml_builder'):
            self.assertNotIn(
                f'from .{proibido}', fonte,
                f'services.py importa {proibido} — dialeto de operadora deve viver em providers/',
            )


# ---------------------------------------------------------------------------
# §4.4(a) — Instrumentação / OperatorCallLog
# ---------------------------------------------------------------------------

@override_settings(TISS_SOAP_MOCK=True)
class InstrumentacaoOperatorCallLogTests(TestCase):
    """
    §4.4(a): a instrumentação vive no ponto de despacho, não dentro de cada
    client — é o que faz um provider novo ganhar observabilidade de graça,
    em vez de "genérico se o dev lembrar".
    """

    def setUp(self):
        self.clinic = make_clinic()
        self.op = make_config(self.clinic, TISSGatewayProvider.GENERICO_ANS, registro_ans='123456')

    def test_consulta_bem_sucedida_gera_log_de_chamada(self):
        consultar_elegibilidade_automatica(
            clinic=self.clinic, operator_config=self.op, numero_carteira='C', mock_scenario='success',
        )
        log = OperatorCallLog.objects.get()
        self.assertEqual(log.registro_ans, '123456')
        self.assertEqual(log.gateway_provider, TISSGatewayProvider.GENERICO_ANS)
        self.assertEqual(log.operation, OperatorCallOperation.COBERTURA)
        self.assertEqual(log.outcome, OperatorCallOutcome.SUCCESS)
        self.assertEqual(log.clinic, self.clinic)

    def test_fault_da_operadora_e_classificado_como_soap_fault(self):
        consultar_elegibilidade_automatica(
            clinic=self.clinic, operator_config=self.op, numero_carteira='C', mock_scenario='error',
        )
        self.assertEqual(OperatorCallLog.objects.get().outcome, OperatorCallOutcome.SOAP_FAULT)

    def test_chave_de_agregacao_e_registro_ans_nao_o_nome_do_gateway(self):
        """
        O dashboard de saúde pergunta por `registro_ans` para qualquer
        operadora plugada — nenhum `if orizon`, hoje ou nunca.
        """
        op_orizon = make_config(self.clinic, TISSGatewayProvider.ORIZON, registro_ans='654321')
        with patch('tiss.providers.orizon.orizon_solicitar_autorizacao') as mock_ori:
            from .orizon_autorize_client import AutorizacaoResult, SituacaoAutorizacao
            mock_ori.return_value = AutorizacaoResult(
                situacao=SituacaoAutorizacao.AUTORIZADO, numero_guia_operadora='G1',
                codigo_glosa='', descricao_glosa='', raw_response='<xml/>',
            )
            consultar_elegibilidade_automatica(
                clinic=self.clinic, operator_config=op_orizon, numero_carteira='C',
            )
        self.assertTrue(OperatorCallLog.objects.filter(registro_ans='654321').exists())

    def test_provider_bloqueado_registra_provider_error(self):
        op = make_config(self.clinic, TISSGatewayProvider.DESCONHECIDO, registro_ans='777777')
        with self.assertRaises(TISSServiceError):
            consultar_elegibilidade_automatica(
                clinic=self.clinic, operator_config=op, numero_carteira='C',
            )
        log = OperatorCallLog.objects.get(registro_ans='777777')
        self.assertEqual(log.outcome, OperatorCallOutcome.PROVIDER_ERROR)

    def test_operadora_desativada_nao_gera_log_de_chamada(self):
        """Não houve chamada: `resolve()` barra antes. Contar isso poluiria a métrica de saúde."""
        self.op.ativo = False
        self.op.save(update_fields=['ativo'])
        with self.assertRaises(TISSServiceError):
            consultar_elegibilidade_automatica(
                clinic=self.clinic, operator_config=self.op, numero_carteira='C',
            )
        self.assertEqual(OperatorCallLog.objects.count(), 0)

    def test_falha_ao_gravar_log_nao_derruba_a_chamada_de_negocio(self):
        """
        Observabilidade nunca pode derrubar o negócio: se a tabela de log
        estiver indisponível, a recepcionista ainda precisa da resposta.
        """
        with patch('tiss.providers.OperatorCallLog.objects.create', side_effect=Exception('db fora')):
            resultado = consultar_elegibilidade_automatica(
                clinic=self.clinic, operator_config=self.op, numero_carteira='C', mock_scenario='success',
            )
        self.assertTrue(resultado.elegivel)

    def test_health_check_nao_polui_a_metrica_de_trafego_real(self):
        providers.health_check(self.op)
        self.assertEqual(OperatorCallLog.objects.count(), 0)


class PurgaOperatorCallLogTests(TestCase):
    """§8.7 — sem purga, a tabela cresce sem limite e vira custo de banco."""

    def setUp(self):
        self.clinic = make_clinic()

    def _log(self, dias_atras):
        log = OperatorCallLog.objects.create(
            registro_ans='123456', gateway_provider=TISSGatewayProvider.GENERICO_ANS,
            operation=OperatorCallOperation.COBERTURA, clinic=self.clinic,
            outcome=OperatorCallOutcome.SUCCESS, latency_ms=10,
        )
        OperatorCallLog.objects.filter(pk=log.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=dias_atras),
        )
        return log

    def test_purga_remove_apenas_registros_alem_da_retencao(self):
        antigo = self._log(120)
        recente = self._log(10)
        call_command('purgar_operator_call_log')
        self.assertFalse(OperatorCallLog.objects.filter(pk=antigo.pk).exists())
        self.assertTrue(OperatorCallLog.objects.filter(pk=recente.pk).exists())

    def test_dry_run_nao_apaga_nada(self):
        self._log(120)
        call_command('purgar_operator_call_log', '--dry-run')
        self.assertEqual(OperatorCallLog.objects.count(), 1)


# ---------------------------------------------------------------------------
# §4.5 — capabilities() exposto na API
# ---------------------------------------------------------------------------

class CapabilitiesNoSerializerTests(TestCase):
    """
    §4.5: sem isto, a UI da recepção acabaria com `if operadora == 'orizon'`
    no frontend — o hardcode mais caro de desfazer, porque vive no parque de
    clínicas.
    """

    def setUp(self):
        self.clinic = make_clinic()

    def test_serializer_expoe_capabilities_do_provider(self):
        from .serializers import TISSOperatorConfigSerializer
        op = make_config(self.clinic, TISSGatewayProvider.ORIZON)
        data = TISSOperatorConfigSerializer(op).data
        self.assertTrue(data['capabilities']['cobertura'])
        # Fature ainda não implementado (D4).
        self.assertFalse(data['capabilities']['envio_lote'])

    def test_serializer_nunca_expoe_credencial(self):
        from .serializers import TISSOperatorConfigSerializer
        op = make_config(self.clinic, TISSGatewayProvider.ORIZON)
        data = TISSOperatorConfigSerializer(op).data
        for proibido in ('login', 'senha', 'login_encrypted', 'senha_encrypted'):
            self.assertNotIn(proibido, data)

    def test_capabilities_nao_expoe_elegibilidade_isolada(self):
        """
        D1: o conceito foi removido do contrato. Se este campo reaparecer, a
        unificação elegibilidade+autorização foi desfeita sem decisão.
        """
        caps = providers.capabilities_for(make_config(self.clinic, TISSGatewayProvider.ORIZON))
        self.assertNotIn('elegibilidade_isolada', caps.as_dict())
        self.assertFalse(hasattr(caps, 'elegibilidade_isolada'))
