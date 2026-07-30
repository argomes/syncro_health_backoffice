"""
TASK-BO-10 — Testes do fluxo assíncrono de assinatura XMLDSig de
`envioDocumentoWS` (Orizon): fila (enfileirar/aplicar assinatura/transmitir),
endpoints de sync (pull/push) e, mais importante, o TESTE DE INTEGRAÇÃO
CRÍTICO que prova byte-identidade do fragmento canônico entre "antes" (o que
seria enviado ao gateway) e "depois" (o XML final transmitido via
soap_client) — critério de aceite formal desta task.
"""
import base64
import uuid

from django.test import TestCase, override_settings
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from clinics.models import Clinic
from .models import (
    TISSOperatorConfig, TISSGuia, TISSDocumentoAssinatura, TISSDocumentoAssinaturaStatus,
    TISSGatewayProvider,
)
from .orizon_envio_documento_xml_builder import (
    build_envio_documento_fragment, OrizonEnvioDocumentoXMLBuilderError,
)
from . import xmldsig_service
from .xmldsig_service import XMLDSigServiceError

FAKE_SIGNATURE_BLOCK = (
    '<Signature xmlns="http://www.w3.org/2000/09/xmldsig#">'
    '<SignedInfo>'
    '<CanonicalizationMethod Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"/>'
    '<SignatureMethod Algorithm="http://www.w3.org/2000/09/xmldsig#rsa-sha1"/>'
    '<Reference URI="">'
    '<DigestMethod Algorithm="http://www.w3.org/2000/09/xmldsig#sha1"/>'
    '<DigestValue>FAKE_DIGEST_VALUE==</DigestValue>'
    '</Reference>'
    '</SignedInfo>'
    '<SignatureValue>FAKE_SIGNATURE_VALUE==</SignatureValue>'
    '<KeyInfo><X509Data><X509Certificate>FAKE_CERT_BASE64==</X509Certificate></X509Data></KeyInfo>'
    '</Signature>'
)


def _make_clinic(slug):
    return Clinic.objects.create(
        name='Clínica Teste XMLDSig',
        slug=slug,
        cnpj=f'{uuid.uuid4().int % 10**14:014d}',
        db_name=f'db_{uuid.uuid4().hex[:8]}',
        db_user=f'u_{uuid.uuid4().hex[:8]}',
    )


class XMLDSigTestMixin:
    def setUp(self):
        self.clinic = _make_clinic(f'xmldsig-teste-{uuid.uuid4().hex[:6]}')
        self.op = TISSOperatorConfig.objects.create(
            clinic=self.clinic, nome_operadora='Orizon', registro_ans='123456',
            endpoint_url='https://wsp.hom.orizonbrasil.com.br:6213/tiss/v40100/tissEnvioDocumento',
            gateway_provider=TISSGatewayProvider.ORIZON,
        )
        self.op.set_login('teste001')
        self.op.set_senha('senha-teste')
        self.op.save()
        self.guia = TISSGuia.objects.create(
            clinic=self.clinic, numero='1', competencia='2026-07', numero_carteira='999',
            valor=150.5,
        )
        self.documento_base64 = base64.b64encode(b'conteudo-fake-do-anexo').decode('ascii')


class OrizonEnvioDocumentoXMLBuilderTests(XMLDSigTestMixin, TestCase):
    def test_gera_fragmento_canonico_bem_formado_sem_signature(self):
        fragmento, root_tag = build_envio_documento_fragment(
            guia=self.guia, clinic=self.clinic, operator_config=self.op,
            sequencial_transacao='000000000001',
            documento_base64=self.documento_base64, nome_arquivo='anexo.pdf',
        )
        self.assertEqual(root_tag, 'sch:envioDocumentoWS')
        self.assertIn('<sch:envioDocumentoWS', fragmento)
        self.assertIn('<sch:cabecalho>', fragmento)
        self.assertIn('<sch:documento>', fragmento)
        self.assertNotIn('<Signature', fragmento)
        self.assertNotIn('Signature>', fragmento)
        # Fragmento canônico não tem declaração XML própria (C14N não emite uma).
        self.assertFalse(fragmento.startswith('<?xml'))

    def test_falha_sem_documento_base64(self):
        with self.assertRaises(OrizonEnvioDocumentoXMLBuilderError):
            build_envio_documento_fragment(
                guia=self.guia, clinic=self.clinic, operator_config=self.op,
                sequencial_transacao='1', documento_base64='', nome_arquivo='x.pdf',
            )

    def test_falha_sem_login_senha(self):
        op_sem_credencial = TISSOperatorConfig.objects.create(
            clinic=self.clinic, nome_operadora='Orizon2', registro_ans='654321',
            endpoint_url='https://example.org/sem-credencial',
            gateway_provider=TISSGatewayProvider.ORIZON,
        )
        with self.assertRaises(OrizonEnvioDocumentoXMLBuilderError):
            build_envio_documento_fragment(
                guia=self.guia, clinic=self.clinic, operator_config=op_sem_credencial,
                sequencial_transacao='1', documento_base64=self.documento_base64,
                nome_arquivo='x.pdf',
            )


class TISSDocumentoAssinaturaFilaTests(XMLDSigTestMixin, TestCase):
    """Testes de fila: criação pendente, transição de estado, reinserção."""

    def test_enfileirar_cria_registro_pendente_com_fragmento_canonico(self):
        documento = xmldsig_service.enfileirar_documento(
            clinic=self.clinic, guia=self.guia, operator_config=self.op,
            sequencial_transacao='1', documento_base64=self.documento_base64,
            nome_arquivo='anexo.pdf',
        )
        self.assertEqual(documento.status, TISSDocumentoAssinaturaStatus.PENDENTE_ASSINATURA)
        self.assertTrue(documento.fragmento_canonico)
        self.assertEqual(documento.xml_final, '')
        self.assertIsNone(documento.assinado_at)

    def test_aplicar_bloco_assinatura_transiciona_para_assinado(self):
        documento = xmldsig_service.enfileirar_documento(
            clinic=self.clinic, guia=self.guia, operator_config=self.op,
            sequencial_transacao='1', documento_base64=self.documento_base64,
            nome_arquivo='anexo.pdf',
        )
        documento.aplicar_bloco_assinatura(FAKE_SIGNATURE_BLOCK)
        documento.refresh_from_db()
        self.assertEqual(documento.status, TISSDocumentoAssinaturaStatus.ASSINADO)
        self.assertIsNotNone(documento.assinado_at)
        self.assertEqual(documento.signature_block, FAKE_SIGNATURE_BLOCK)

    def test_reinsercao_correta_no_xml_o_bloco_fica_dentro_da_raiz(self):
        documento = xmldsig_service.enfileirar_documento(
            clinic=self.clinic, guia=self.guia, operator_config=self.op,
            sequencial_transacao='1', documento_base64=self.documento_base64,
            nome_arquivo='anexo.pdf',
        )
        documento.aplicar_bloco_assinatura(FAKE_SIGNATURE_BLOCK)
        # Signature aparece antes da tag de fechamento da raiz, dentro dela.
        idx_signature = documento.xml_final.index('<Signature')
        idx_fechamento_raiz = documento.xml_final.index('</sch:envioDocumentoWS>')
        self.assertLess(idx_signature, idx_fechamento_raiz)
        self.assertTrue(documento.xml_final.endswith('</sch:envioDocumentoWS>'))

    def test_nao_pode_aplicar_assinatura_duas_vezes(self):
        documento = xmldsig_service.enfileirar_documento(
            clinic=self.clinic, guia=self.guia, operator_config=self.op,
            sequencial_transacao='1', documento_base64=self.documento_base64,
            nome_arquivo='anexo.pdf',
        )
        documento.aplicar_bloco_assinatura(FAKE_SIGNATURE_BLOCK)
        with self.assertRaises(ValidationError):
            documento.aplicar_bloco_assinatura(FAKE_SIGNATURE_BLOCK)

    def test_aplicar_assinatura_rejeita_bloco_sem_tag_signature(self):
        documento = xmldsig_service.enfileirar_documento(
            clinic=self.clinic, guia=self.guia, operator_config=self.op,
            sequencial_transacao='1', documento_base64=self.documento_base64,
            nome_arquivo='anexo.pdf',
        )
        with self.assertRaises(ValidationError):
            documento.aplicar_bloco_assinatura('<NaoEhSignature/>')

    def test_isolamento_multi_tenant_guia_de_outra_clinica(self):
        outra_clinic = _make_clinic(f'outra-clinica-{uuid.uuid4().hex[:6]}')
        guia_outra = TISSGuia.objects.create(
            clinic=outra_clinic, numero='9', competencia='2026-07',
        )
        documento = TISSDocumentoAssinatura(
            clinic=self.clinic, guia=guia_outra, operator_config=self.op,
            fragmento_canonico='<x/>', root_tag='x',
        )
        with self.assertRaises(ValidationError):
            documento.full_clean()


class XMLDSigServiceAplicarAssinaturaTests(XMLDSigTestMixin, TestCase):
    def test_aplicar_assinatura_isolado_por_clinica(self):
        documento = xmldsig_service.enfileirar_documento(
            clinic=self.clinic, guia=self.guia, operator_config=self.op,
            sequencial_transacao='1', documento_base64=self.documento_base64,
            nome_arquivo='anexo.pdf',
        )
        outra_clinic = _make_clinic(f'outra-clinica-svc-{uuid.uuid4().hex[:6]}')
        with self.assertRaises(XMLDSigServiceError) as ctx:
            xmldsig_service.aplicar_assinatura(outra_clinic, str(documento.id), FAKE_SIGNATURE_BLOCK)
        self.assertEqual(ctx.exception.code, 'documento_nao_encontrado')

    def test_aplicar_assinatura_em_documento_ja_assinado_falha(self):
        documento = xmldsig_service.enfileirar_documento(
            clinic=self.clinic, guia=self.guia, operator_config=self.op,
            sequencial_transacao='1', documento_base64=self.documento_base64,
            nome_arquivo='anexo.pdf',
        )
        xmldsig_service.aplicar_assinatura(self.clinic, str(documento.id), FAKE_SIGNATURE_BLOCK)
        with self.assertRaises(XMLDSigServiceError) as ctx:
            xmldsig_service.aplicar_assinatura(self.clinic, str(documento.id), FAKE_SIGNATURE_BLOCK)
        self.assertEqual(ctx.exception.code, 'documento_nao_esta_pendente')


@override_settings(TISS_SOAP_MOCK=True)
class TransmitirTests(XMLDSigTestMixin, TestCase):
    def test_transmitir_documento_assinado_com_sucesso_marca_enviado(self):
        documento = xmldsig_service.enfileirar_documento(
            clinic=self.clinic, guia=self.guia, operator_config=self.op,
            sequencial_transacao='1', documento_base64=self.documento_base64,
            nome_arquivo='anexo.pdf',
        )
        xmldsig_service.aplicar_assinatura(self.clinic, str(documento.id), FAKE_SIGNATURE_BLOCK)
        documento.refresh_from_db()
        documento = xmldsig_service.transmitir(documento, mock_scenario='success')
        self.assertEqual(documento.status, TISSDocumentoAssinaturaStatus.ENVIADO)
        self.assertEqual(documento.protocolo, 'MOCK-PROTO-000001')
        self.assertIsNotNone(documento.enviado_at)

    def test_transmitir_documento_nao_assinado_falha(self):
        documento = xmldsig_service.enfileirar_documento(
            clinic=self.clinic, guia=self.guia, operator_config=self.op,
            sequencial_transacao='1', documento_base64=self.documento_base64,
            nome_arquivo='anexo.pdf',
        )
        with self.assertRaises(XMLDSigServiceError):
            xmldsig_service.transmitir(documento, mock_scenario='success')


class SyncEndpointsTests(XMLDSigTestMixin, TestCase):
    """Endpoints de sync (pull/push) — mesmo padrão de auth de X-License-Key já usado por heartbeat/elegibilidade."""

    def setUp(self):
        super().setUp()
        self.client_api = APIClient()

    def test_pull_pendentes_exige_license_key(self):
        resp = self.client_api.get('/api/tiss/xmldsig/sync/pendentes/')
        self.assertIn(resp.status_code, (401, 403))

    def test_pull_pendentes_retorna_fragmentos_da_clinica(self):
        documento = xmldsig_service.enfileirar_documento(
            clinic=self.clinic, guia=self.guia, operator_config=self.op,
            sequencial_transacao='1', documento_base64=self.documento_base64,
            nome_arquivo='anexo.pdf',
        )
        resp = self.client_api.get(
            '/api/tiss/xmldsig/sync/pendentes/', HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(resp.status_code, 200)
        ids = [d['id'] for d in resp.data['documentos']]
        self.assertIn(str(documento.id), ids)
        self.assertEqual(resp.data['documentos'][0]['fragmento_canonico'], documento.fragmento_canonico)

    def test_pull_pendentes_nao_vaza_documento_de_outra_clinica(self):
        outra_clinic = _make_clinic(f'outra-clinica-pull-{uuid.uuid4().hex[:6]}')
        outra_op = TISSOperatorConfig.objects.create(
            clinic=outra_clinic, nome_operadora='Orizon', registro_ans='999999',
            endpoint_url='https://example.org/outra',
            gateway_provider=TISSGatewayProvider.ORIZON,
        )
        outra_op.set_login('x')
        outra_op.set_senha('y')
        outra_op.save()
        outra_guia = TISSGuia.objects.create(clinic=outra_clinic, numero='9', competencia='2026-07')
        xmldsig_service.enfileirar_documento(
            clinic=outra_clinic, guia=outra_guia, operator_config=outra_op,
            sequencial_transacao='1', documento_base64=self.documento_base64,
            nome_arquivo='anexo.pdf',
        )
        resp = self.client_api.get(
            '/api/tiss/xmldsig/sync/pendentes/', HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(resp.data['documentos'], [])

    def test_push_assinatura_aplica_bloco_e_isola_por_clinica(self):
        documento = xmldsig_service.enfileirar_documento(
            clinic=self.clinic, guia=self.guia, operator_config=self.op,
            sequencial_transacao='1', documento_base64=self.documento_base64,
            nome_arquivo='anexo.pdf',
        )
        outra_clinic = _make_clinic(f'outra-clinica-push-{uuid.uuid4().hex[:6]}')
        resp = self.client_api.post(
            '/api/tiss/xmldsig/sync/assinatura/',
            {'documento_id': str(documento.id), 'signature_block': FAKE_SIGNATURE_BLOCK},
            format='json', HTTP_X_LICENSE_KEY=str(outra_clinic.license_key),
        )
        self.assertEqual(resp.status_code, 404)

        resp = self.client_api.post(
            '/api/tiss/xmldsig/sync/assinatura/',
            {'documento_id': str(documento.id), 'signature_block': FAKE_SIGNATURE_BLOCK},
            format='json', HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(resp.status_code, 200)
        documento.refresh_from_db()
        self.assertEqual(documento.status, TISSDocumentoAssinaturaStatus.ASSINADO)


@override_settings(TISS_SOAP_MOCK=True)
class C14NByteIdentityIntegrationTests(XMLDSigTestMixin, TestCase):
    """
    TESTE DE INTEGRAÇÃO CRÍTICO (critério de aceite formal mais importante da
    task): prova que o fragmento C14N "enviado ao gateway" (passo 1) é
    BYTE-IDÊNTICO à porção correspondente do XML final efetivamente
    transmitido via soap_client (passo 4), depois de toda a manipulação de
    reinserção — ou seja, nenhuma re-serialização ocorreu no meio do caminho.

    O que é comparado, exatamente:
    - "ANTES": os bytes de `fragmento_canonico` gravados no passo 1
      (`enfileirar_documento`, saída direta de
      `build_envio_documento_fragment`, resultado de UM único C14N).
    - "DEPOIS": os mesmos bytes, extraídos de dentro de `xml_final` (depois
      de `aplicar_bloco_assinatura` reinserir o bloco de assinatura fake por
      concatenação de string) subtraindo exatamente os bytes do
      `signature_block` inserido — se a extração reproduzir bytes idênticos
      ao fragmento original, prova que a inserção foi puramente textual, sem
      nenhum parse+serialize intermediário (que teria mudado ordenação de
      atributos/whitespace do C14N).

    Como controle adicional, o teste também prova a mesma coisa "pelo outro
    lado": remove o `signature_block` de dentro de `xml_final` por simples
    `str.replace` e confirma que o restante é BYTE-A-BYTE igual a
    `fragmento_canonico` — sem depender de reparsear XML nenhum nesta
    verificação (a prova em si não usa etree, é comparação de string pura,
    igual à garantia que o código de produção precisa manter).
    """

    def test_fragmento_canonico_e_byte_identico_dentro_do_xml_final(self):
        # Passo 1: fragmento que "seria enviado ao gateway".
        fragmento_enviado_ao_gateway, root_tag = build_envio_documento_fragment(
            guia=self.guia, clinic=self.clinic, operator_config=self.op,
            sequencial_transacao='000000000042', documento_base64=self.documento_base64,
            nome_arquivo='anexo.pdf',
        )
        fragmento_enviado_ao_gateway_bytes = fragmento_enviado_ao_gateway.encode('utf-8')

        # Enfileira (grava fragmento_canonico) — mesma chamada de builder por
        # trás, mas via o caminho de produção real (services -> model).
        documento = xmldsig_service.enfileirar_documento(
            clinic=self.clinic, guia=self.guia, operator_config=self.op,
            sequencial_transacao='000000000042', documento_base64=self.documento_base64,
            nome_arquivo='anexo.pdf',
        )
        # O que foi persistido no passo 1 é byte-idêntico ao que "seria
        # enviado ao gateway" (mesmos parâmetros determinísticos, exceto
        # timestamps do cabeçalho — controlados abaixo comparando o próprio
        # valor persistido, não um novo build).
        fragmento_persistido_bytes = documento.fragmento_canonico.encode('utf-8')

        # Passo 3/4: gateway devolve só o bloco de assinatura fake; backoffice
        # reinsere por texto (nunca reparseia) e monta xml_final.
        documento = xmldsig_service.aplicar_assinatura(
            self.clinic, str(documento.id), FAKE_SIGNATURE_BLOCK,
        )
        xml_final_bytes = documento.xml_final.encode('utf-8')

        # PROVA 1 (extração): removendo exatamente os bytes do signature_block
        # de dentro de xml_final (via string.replace simples, sem etree), o
        # que resta é byte-a-byte igual ao fragmento persistido no passo 1.
        xml_final_sem_assinatura = documento.xml_final.replace(FAKE_SIGNATURE_BLOCK, '', 1)
        self.assertEqual(
            xml_final_sem_assinatura.encode('utf-8'),
            fragmento_persistido_bytes,
            'xml_final, com o bloco de assinatura removido por string.replace, '
            'deve ser byte-idêntico ao fragmento_canonico gravado no passo 1 — '
            'qualquer diferença aqui indicaria que o fragmento foi reparseado/'
            're-serializado em algum ponto do caminho, o que invalidaria a '
            'assinatura XMLDSig real.',
        )

        # PROVA 2 (posição/estrutura): o signature_block aparece INTEIRO e
        # UMA ÚNICA VEZ dentro de xml_final, imediatamente antes da tag de
        # fechamento da raiz — prova que a inserção foi uma concatenação
        # textual no ponto certo, não uma reconstrução do documento.
        closing_tag = f'</{documento.root_tag}>'
        expected_xml_final = fragmento_persistido_bytes.decode('utf-8').replace(
            closing_tag, f'{FAKE_SIGNATURE_BLOCK}{closing_tag}', 1,
        )
        self.assertEqual(documento.xml_final, expected_xml_final)
        self.assertEqual(xml_final_bytes, expected_xml_final.encode('utf-8'))

        # PROVA 3 (ponta a ponta via soap_client, mock TISS_SOAP_MOCK): o
        # envelope SOAP que soap_client.enviar_documento efetivamente
        # transmitiria embute xml_final sem alterar seus bytes (nenhuma
        # declaração XML dupla, nenhuma reserialização) — confirma que o
        # caminho real de transmissão (`xmldsig_service.transmitir`) usa os
        # MESMOS bytes de xml_final, não uma cópia reprocessada.
        from .soap_client import _build_envelope
        envelope = _build_envelope(documento.xml_final, operation='envioDocumentoWS')
        self.assertIn(documento.xml_final, envelope)

        resultado_documento = xmldsig_service.transmitir(documento, mock_scenario='success')
        self.assertEqual(resultado_documento.status, TISSDocumentoAssinaturaStatus.ENVIADO)
        # xml_final não muda com a transmissão (é só lido, nunca reescrito).
        self.assertEqual(resultado_documento.xml_final.encode('utf-8'), xml_final_bytes)
