"""
BO-08.3 — Orquestra o envio de um TISSLote: valida XSD, calcula hash,
envia SOAP (real ou mock) e persiste o resultado. Usado pela ViewSet
action `enviar` (tiss/views.py).
"""
import logging
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from django.db import transaction

from .models import (
    TISSLote, TISSLoteStatus, TISSGuia, TISSGuiaStatus, TISSGlosa,
    TISSElegibilidadeConsulta, TISSElegibilidadeOrigem, TISSElegibilidadeStatus,
)
from .xml_builder import build_lote_xml, XMLBuilderError
from .xml_validator import validate_xml, XMLValidatorError
from .soap_client import (
    enviar_lote as soap_enviar_lote,
    verificar_elegibilidade as soap_verificar_elegibilidade,
    SOAPClientError, SOAPSuccessResult, SOAPFaultResult, ElegibilidadeResult,
)

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


def _build_pedido_elegibilidade_xml(clinic, numero_carteira: str) -> str:
    """
    Monta o fragmento pedidoElegibilidade (ct_elegibilidadeVerifica) mínimo:
    dadosPrestador (choice — usamos cnpjContratado, sempre disponível via
    Clinic.cnpj) + numeroCarteira (únicos campos obrigatórios no XSD; os
    demais são todos minOccurs="0").

    BACFF-013: mesma ressalva já registrada para o request de envio de lote
    — o "wrapping" exato da operação (se o corpo do SOAP deveria embrulhar
    isto dentro de pedidoElegibilidadeWS/cabecalho, e não só o fragmento
    cru) ainda não foi confirmado contra uma operadora real, só contra o
    padrão ANS publicado. Mantém o mesmo padrão já usado por enviar_lote
    (fragmento de negócio direto, sem o wrapper de mensagem) por
    consistência — ver nota em tiss_soap_plan.md sobre essa limitação.
    """
    cnpj_limpo = (clinic.cnpj or '').replace('.', '').replace('/', '').replace('-', '').strip()
    return (
        '<pedidoElegibilidade>'
        '<dadosPrestador>'
        f'<cnpjContratado>{escape(cnpj_limpo)}</cnpjContratado>'
        '</dadosPrestador>'
        f'<numeroCarteira>{escape(numero_carteira)}</numeroCarteira>'
        '</pedidoElegibilidade>'
    )


@dataclass
class ElegibilidadeRespostaCompleta:
    """
    BACFF-AVULSA-01: o resultado COMPLETO (conteúdo clínico) da consulta —
    devolvido na resposta HTTP síncrona ao Edge Gateway, mas nunca
    persistido no banco central. Quem chama (views.py) serializa isto
    diretamente, sem passar pelo model/DB. A cópia duradoura fica no banco
    LOCAL da clínica (Edge Gateway), responsabilidade de quem consome esta
    resposta — não deste serviço.
    """
    elegivel: bool
    numero_carteira: str
    beneficiario_nome: str
    origem: str
    motivos_negativa: list = field(default_factory=list)
    numero_guia_operadora: str = ''
    erro_mensagem: str = ''


def _log_elegibilidade(clinic, operator_config, appointment_id, origem, status, erro_mensagem=''):
    """
    Persiste SÓ o log operacional (BACFF-AVULSA-01) — sem numero_carteira,
    beneficiario_nome, elegivel, motivos_negativa nem XML. `erro_mensagem`
    aqui é sempre técnica (código/descrição de falha de transporte ou fault
    da operadora), nunca conteúdo do beneficiário — quem monta essa string
    nas funções abaixo é responsável por essa garantia.
    """
    return TISSElegibilidadeConsulta.objects.create(
        clinic=clinic,
        operator_config=operator_config,
        appointment_id=appointment_id,
        origem=origem,
        status=status,
        erro_mensagem=erro_mensagem,
    )


def consultar_elegibilidade_automatica(
    clinic, operator_config, numero_carteira: str,
    beneficiario_nome: str = '', appointment_id: str = '',
    mock_scenario: str = 'success',
) -> ElegibilidadeRespostaCompleta:
    """
    Consulta síncrona de elegibilidade via SOAP (tissVerificaElegibilidade_
    Operation) — diferente de enviar_lote, não há estado intermediário
    (BACFF-013: confirmado que elegibilidade não tem operação de polling/
    status na spec ANS, ao contrário de lote).

    BACFF-AVULSA-01: só persiste um log OPERACIONAL (sucesso/falha) no banco
    central — o conteúdo clínico completo (elegível, motivos, nome do
    beneficiário) só existe na resposta devolvida daqui, nunca no banco
    central. Quem precisa do histórico completo persiste localmente (Edge
    Gateway).
    """
    xml_pedido = _build_pedido_elegibilidade_xml(clinic, numero_carteira)

    try:
        resultado = soap_verificar_elegibilidade(
            endpoint_url=operator_config.endpoint_url,
            xml_mensagem_tiss=xml_pedido,
            mock_scenario=mock_scenario,
        )
    except SOAPClientError as exc:
        erro = f'falha_soap: {exc}'
        _log_elegibilidade(clinic, operator_config, appointment_id, TISSElegibilidadeOrigem.AUTOMATICA, TISSElegibilidadeStatus.FALHA_TRANSPORTE, erro)
        return ElegibilidadeRespostaCompleta(
            elegivel=False, numero_carteira=numero_carteira, beneficiario_nome=beneficiario_nome,
            origem=TISSElegibilidadeOrigem.AUTOMATICA, erro_mensagem=erro,
        )

    if isinstance(resultado, ElegibilidadeResult):
        _log_elegibilidade(clinic, operator_config, appointment_id, TISSElegibilidadeOrigem.AUTOMATICA, TISSElegibilidadeStatus.SUCESSO)
        return ElegibilidadeRespostaCompleta(
            elegivel=resultado.elegivel,
            numero_carteira=numero_carteira,
            beneficiario_nome=beneficiario_nome or resultado.nome_beneficiario,
            origem=TISSElegibilidadeOrigem.AUTOMATICA,
            motivos_negativa=[
                {'codigo': codigo, 'descricao': descricao}
                for codigo, descricao in resultado.motivos_negativa
            ],
        )

    # SOAPFaultResult — operadora rejeitou a PRÓPRIA consulta (ex.: prestador
    # não autorizado), não uma resposta sobre o beneficiário.
    erro = f'{resultado.codigo_erro}: {resultado.descricao_erro}'
    _log_elegibilidade(clinic, operator_config, appointment_id, TISSElegibilidadeOrigem.AUTOMATICA, TISSElegibilidadeStatus.FALHA_OPERADORA, erro)
    return ElegibilidadeRespostaCompleta(
        elegivel=False, numero_carteira=numero_carteira, beneficiario_nome=beneficiario_nome,
        origem=TISSElegibilidadeOrigem.AUTOMATICA, erro_mensagem=erro,
    )


def registrar_elegibilidade_manual(
    clinic, operator_config, numero_carteira: str, numero_guia_operadora: str,
    elegivel: bool, beneficiario_nome: str = '', appointment_id: str = '',
) -> ElegibilidadeRespostaCompleta:
    """
    BACFF-013: fallback de primeira classe, não uma exceção informal — a
    recepcionista ligou/acessou o portal da operadora diretamente e digitou
    o número da guia gerada manualmente. numero_guia_operadora é obrigatório.

    BACFF-AVULSA-01: mesmo tratamento de consultar_elegibilidade_automatica
    — log operacional central, conteúdo completo só na resposta.
    """
    if not numero_guia_operadora:
        raise TISSServiceError('numero_guia_operadora_obrigatorio', 'Registro manual exige o número da guia da operadora')

    _log_elegibilidade(clinic, operator_config, appointment_id, TISSElegibilidadeOrigem.MANUAL, TISSElegibilidadeStatus.SUCESSO)
    return ElegibilidadeRespostaCompleta(
        elegivel=elegivel,
        numero_carteira=numero_carteira,
        beneficiario_nome=beneficiario_nome,
        origem=TISSElegibilidadeOrigem.MANUAL,
        numero_guia_operadora=numero_guia_operadora,
    )
