"""
BO-08.3 — Orquestra o envio de um TISSLote: valida XSD, calcula hash,
envia SOAP (real ou mock) e persiste o resultado. Usado pela ViewSet
action `enviar` (tiss/views.py).
"""
import logging

from django.db import transaction

from .models import TISSLote, TISSLoteStatus, TISSGuia, TISSGuiaStatus, TISSGlosa
from .xml_builder import build_lote_xml, XMLBuilderError
from .xml_validator import validate_xml, XMLValidatorError
from .soap_client import enviar_lote as soap_enviar_lote, SOAPClientError, SOAPSuccessResult, SOAPFaultResult

logger = logging.getLogger(__name__)


class TISSServiceError(Exception):
    def __init__(self, code: str, message: str = ''):
        self.code = code
        super().__init__(message or code)


def criar_lote(clinic, operator_config, competencia: str) -> TISSLote:
    """
    Cria um TISSLote com numeroLote sequencial por (clínica, operadora).
    select_for_update dentro da transaction evita corrida entre duas
    requisições concorrentes de criação de lote para a mesma clínica+operadora
    (o único cenário de corrida real aqui, já que o sequencial não é global).
    """
    with transaction.atomic():
        numero_lote = TISSLote.next_numero_lote(clinic, operator_config)
        lote = TISSLote.objects.create(
            clinic=clinic,
            operator_config=operator_config,
            numero_lote=numero_lote,
            competencia=competencia,
            status=TISSLoteStatus.MONTANDO,
        )
    return lote


def enviar_lote(lote: TISSLote, guia_ids: list = None, mock_scenario: str = 'success') -> TISSLote:
    """
    Fluxo completo: busca guias do lote (ou as informadas), monta XML, valida
    contra XSD, calcula MD5, envia SOAP, persiste resultado e cria TISSGlosa
    se houver rejeição. Sempre isolado à clínica do próprio lote (guias vêm
    de `lote.guias` ou de `TISSGuia.objects.filter(clinic=lote.clinic, ...)`
    — nunca de outra clínica).
    """
    guias = list(lote.guias.all()) if guia_ids is None else list(
        TISSGuia.objects.filter(clinic=lote.clinic, id__in=guia_ids)
    )
    if not guias:
        raise TISSServiceError('lote_sem_guias', 'Lote não possui guias associadas')

    sequencial_transacao = f'{lote.numero_lote:012d}'

    try:
        xml_completo, hash_md5 = build_lote_xml(
            lote=lote,
            guias=guias,
            clinic=lote.clinic,
            operator_config=lote.operator_config,
            sequencial_transacao=sequencial_transacao,
        )
    except XMLBuilderError as exc:
        lote.status = TISSLoteStatus.ERRO_ENVIO
        lote.erro_mensagem = f'erro_ao_montar_xml: {exc}'
        lote.save(update_fields=['status', 'erro_mensagem', 'updated_at'])
        raise TISSServiceError('xml_builder_failed', str(exc)) from exc

    try:
        issues = validate_xml(xml_completo)
    except XMLValidatorError as exc:
        lote.status = TISSLoteStatus.ERRO_ENVIO
        lote.erro_mensagem = f'erro_validador_xsd: {exc}'
        lote.save(update_fields=['status', 'erro_mensagem', 'updated_at'])
        raise TISSServiceError('xsd_validator_unavailable', str(exc)) from exc

    if issues:
        mensagens = '; '.join(str(i) for i in issues[:10])
        lote.status = TISSLoteStatus.ERRO_ENVIO
        lote.xml_enviado = xml_completo
        lote.hash_epilogo = hash_md5
        lote.erro_mensagem = f'xml_invalido_contra_xsd: {mensagens}'
        lote.save(update_fields=['status', 'xml_enviado', 'hash_epilogo', 'erro_mensagem', 'updated_at'])
        raise TISSServiceError('xml_schema_invalid', mensagens)

    lote.status = TISSLoteStatus.VALIDADO
    lote.xml_enviado = xml_completo
    lote.hash_epilogo = hash_md5
    lote.save(update_fields=['status', 'xml_enviado', 'hash_epilogo', 'updated_at'])

    lote.status = TISSLoteStatus.ENVIANDO
    lote.save(update_fields=['status', 'updated_at'])

    try:
        resultado = soap_enviar_lote(
            endpoint_url=lote.operator_config.endpoint_url,
            xml_mensagem_tiss=xml_completo,
            mock_scenario=mock_scenario,
        )
    except SOAPClientError as exc:
        lote.status = TISSLoteStatus.ERRO_ENVIO
        lote.erro_mensagem = f'falha_soap: {exc}'
        lote.save(update_fields=['status', 'erro_mensagem', 'updated_at'])
        raise TISSServiceError('soap_send_failed', str(exc)) from exc

    if isinstance(resultado, SOAPSuccessResult):
        lote.status = TISSLoteStatus.ENVIADO
        lote.protocolo = resultado.protocolo
        lote.xml_recebido = resultado.raw_response
        lote.save(update_fields=['status', 'protocolo', 'xml_recebido', 'updated_at'])
        TISSGuia.objects.filter(id__in=[g.id for g in guias]).update(status=TISSGuiaStatus.ENVIADA, lote=lote)
        return lote

    if isinstance(resultado, SOAPFaultResult):
        lote.status = TISSLoteStatus.ERRO_ENVIO
        lote.xml_recebido = resultado.raw_response
        lote.erro_mensagem = f'{resultado.codigo_erro}: {resultado.descricao_erro}'
        lote.save(update_fields=['status', 'xml_recebido', 'erro_mensagem', 'updated_at'])
        for guia in guias:
            guia.status = TISSGuiaStatus.GLOSADA
            guia.lote = lote
            guia.save(update_fields=['status', 'lote', 'updated_at'])
            TISSGlosa.objects.create(
                guia=guia,
                codigo=resultado.codigo_erro,
                descricao=resultado.descricao_erro,
                valor_glosado=guia.valor,
            )
        raise TISSServiceError('soap_fault', f'{resultado.codigo_erro}: {resultado.descricao_erro}')

    raise TISSServiceError('resultado_soap_desconhecido')
