"""
BO-08.3 — Orquestra o envio de um TISSLote: valida XSD, calcula hash,
envia SOAP (real ou mock) e persiste o resultado. Usado pela ViewSet
action `enviar` (tiss/views.py).
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from xml.sax.saxutils import escape

from django.db import transaction

from .models import (
    TISSLote, TISSLoteStatus, TISSGuia, TISSGuiaStatus, TISSGlosa,
    TISSElegibilidadeConsulta, TISSElegibilidadeOrigem, TISSElegibilidadeStatus,
    TISSGatewayProvider,
)
from .xml_builder import build_lote_xml, XMLBuilderError
from .xml_validator import validate_xml, XMLValidatorError
from .soap_client import (
    enviar_lote as soap_enviar_lote,
    verificar_elegibilidade as soap_verificar_elegibilidade,
    SOAPClientError, SOAPSuccessResult, SOAPFaultResult, ElegibilidadeResult,
)
from .orizon_autorize_xml_builder import build_solicitacao_procedimento_xml, OrizonAutorizeXMLBuilderError
from .orizon_autorize_client import (
    solicitar_autorizacao as orizon_solicitar_autorizacao,
    OrizonAutorizeClientError, AutorizacaoResult, SOAPFaultResult as OrizonSOAPFaultResult,
    SituacaoAutorizacao,
)

logger = logging.getLogger(__name__)


class TISSServiceError(Exception):
    def __init__(self, code: str, message: str = ''):
        self.code = code
        super().__init__(message or code)


def _checar_operadora_ativa(operator_config) -> None:
    """
    BACFF-AVULSA-12: `TISSOperatorConfig.ativo` precisa ser consultado ANTES
    de qualquer I/O de rede com a operadora. Até esta correção, o campo
    existia no model (e tinha até índice `(clinic, ativo)`) mas nenhum
    ponto de despacho o lia — desativar uma operadora no admin não parava
    nenhuma chamada real.

    Chamado no topo de todo caminho que fala SOAP com a operadora
    (elegibilidade automática, envio de lote). NÃO chamado por
    `registrar_elegibilidade_manual` — o registro manual não faz I/O de
    rede com a operadora (a recepcionista já obteve a informação por
    telefone/portal), e deve continuar funcionando mesmo com a operadora
    desativada: desativar a integração automática não deveria travar o
    trabalho manual da recepção.
    """
    if not operator_config.ativo:
        raise TISSServiceError(
            'operadora_desativada',
            f'Operadora {operator_config.registro_ans} está desativada para esta clínica',
        )


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
    _checar_operadora_ativa(lote.operator_config)

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

    # BACFF-AVULSA-13: mesmo mecanismo de resolução por `gateway_provider`
    # já usado em `consultar_elegibilidade_automatica` — antes desta
    # correção, `enviar_lote` chamava `soap_enviar_lote` (client genérico
    # ANS) incondicionalmente, mesmo para clínicas configuradas com Orizon,
    # mandando o lote para o endpoint errado silenciosamente. A Orizon
    # ainda não tem client de envio de lote próprio implementado (Fature —
    # BACFF-014, P0 em separado); em vez de mandar o payload no dialeto
    # errado para uma operadora real (o que gera glosa e retrabalho de
    # faturamento), falhamos alto e explicitamente até o client Fature
    # existir. Nunca fazer fallback silencioso para o genérico.
    if lote.operator_config.gateway_provider != TISSGatewayProvider.GENERICO_ANS:
        lote.status = TISSLoteStatus.ERRO_ENVIO
        lote.erro_mensagem = (
            f'provider_lote_nao_implementado: envio de lote para '
            f'gateway_provider={lote.operator_config.gateway_provider} ainda não tem client próprio'
        )
        lote.save(update_fields=['status', 'erro_mensagem', 'updated_at'])
        raise TISSServiceError(
            'provider_lote_nao_implementado',
            f'Envio de lote não implementado para o provider "{lote.operator_config.gateway_provider}"',
        )

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
    Consulta síncrona de elegibilidade — despacha para o client certo
    conforme `operator_config.gateway_provider` (BACFF-014). Antes desta
    task, TODA operadora era tratada como genérico ANS mesmo quando
    configurada como Orizon — o client Orizon existia mas nunca era
    chamado pelo fluxo real. Sem estado intermediário em nenhum dos dois
    casos (BACFF-013: elegibilidade não tem polling na spec ANS, ao
    contrário de lote).

    BACFF-AVULSA-01: só persiste um log OPERACIONAL (sucesso/falha) no banco
    central — o conteúdo clínico completo (elegível, motivos, nome do
    beneficiário) só existe na resposta devolvida daqui, nunca no banco
    central. Quem precisa do histórico completo persiste localmente (Edge
    Gateway).
    """
    _checar_operadora_ativa(operator_config)

    if operator_config.gateway_provider == TISSGatewayProvider.ORIZON:
        return _consultar_elegibilidade_orizon(
            clinic, operator_config, numero_carteira, beneficiario_nome, appointment_id, mock_scenario,
        )
    return _consultar_elegibilidade_generico_ans(
        clinic, operator_config, numero_carteira, beneficiario_nome, appointment_id, mock_scenario,
    )


def _consultar_elegibilidade_generico_ans(
    clinic, operator_config, numero_carteira: str,
    beneficiario_nome: str, appointment_id: str, mock_scenario: str,
) -> ElegibilidadeRespostaCompleta:
    """
    Client genérico contra o WSDL/schema padrão ANS (tissVerificaElegibilidade_
    Operation) — comportamento pré-existente, mantido como default e
    fallback para qualquer `gateway_provider` que não seja 'orizon'.
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


def _consultar_elegibilidade_orizon(
    clinic, operator_config, numero_carteira: str,
    beneficiario_nome: str, appointment_id: str, mock_scenario: str,
) -> ElegibilidadeRespostaCompleta:
    """
    BACFF-014: a Orizon não tem operação de elegibilidade isolada — o
    manual técnico documenta que a elegibilidade do beneficiário é
    verificada DENTRO do próprio pedido de autorização
    (solicitacaoProcedimentoWS/solicitacaoSP-SADT), resposta em
    autorizacaoProcedimentoWS. Por isso não reaproveitamos
    `soap_verificar_elegibilidade`/`ElegibilidadeResult` (assinatura do
    client genérico) — chamamos `orizon_solicitar_autorizacao` e
    normalizamos o retorno (`AutorizacaoResult`/`SOAPFaultResult` do módulo
    Orizon) para o mesmo formato `ElegibilidadeRespostaCompleta` que este
    serviço já devolve para o caminho genérico.

    Este endpoint não recebe uma guia real (nem procedimentos) — só
    numero_carteira/appointment_id — então montamos um objeto guia mínimo
    (não persistido) só para reaproveitar `build_solicitacao_procedimento_xml`
    sem duplicar a lógica de montagem de XML. `mock_scenario` aqui usa o
    vocabulário do client Orizon ('autorizado'/'negado'/'em_analise'/
    'fault'), diferente do client genérico ('success'/'negativa'/'error') —
    ver mapeamento em `_ORIZON_MOCK_SCENARIO_MAP` abaixo.
    """
    guia_transiente = TISSGuia(
        clinic=clinic,
        appointment_id=appointment_id,
        numero=appointment_id or 'ELEGIBILIDADE',
        numero_carteira=numero_carteira,
        procedimentos=[],
    )
    sequencial_transacao = datetime.now().strftime('%y%m%d%H%M%S')

    try:
        xml_solicitacao, _hash = build_solicitacao_procedimento_xml(
            guia=guia_transiente, clinic=clinic, operator_config=operator_config,
            sequencial_transacao=sequencial_transacao,
        )
    except OrizonAutorizeXMLBuilderError as exc:
        erro = f'falha_montagem_xml_orizon: {exc}'
        _log_elegibilidade(clinic, operator_config, appointment_id, TISSElegibilidadeOrigem.AUTOMATICA, TISSElegibilidadeStatus.FALHA_TRANSPORTE, erro)
        return ElegibilidadeRespostaCompleta(
            elegivel=False, numero_carteira=numero_carteira, beneficiario_nome=beneficiario_nome,
            origem=TISSElegibilidadeOrigem.AUTOMATICA, erro_mensagem=erro,
        )

    orizon_mock_scenario = _ORIZON_MOCK_SCENARIO_MAP.get(mock_scenario, mock_scenario)

    try:
        resultado = orizon_solicitar_autorizacao(
            endpoint_url=operator_config.endpoint_url,
            xml_solicitacao=xml_solicitacao,
            mock_scenario=orizon_mock_scenario,
        )
    except OrizonAutorizeClientError as exc:
        erro = f'falha_soap_orizon: {exc}'
        _log_elegibilidade(clinic, operator_config, appointment_id, TISSElegibilidadeOrigem.AUTOMATICA, TISSElegibilidadeStatus.FALHA_TRANSPORTE, erro)
        return ElegibilidadeRespostaCompleta(
            elegivel=False, numero_carteira=numero_carteira, beneficiario_nome=beneficiario_nome,
            origem=TISSElegibilidadeOrigem.AUTOMATICA, erro_mensagem=erro,
        )

    if isinstance(resultado, AutorizacaoResult):
        _log_elegibilidade(clinic, operator_config, appointment_id, TISSElegibilidadeOrigem.AUTOMATICA, TISSElegibilidadeStatus.SUCESSO)
        # A Orizon não devolve nome do beneficiário nesta operação (ao
        # contrário do client genérico) — beneficiario_nome só reflete o
        # que já veio do chamador. `situacao='em_analise'` não é uma
        # negativa nem tem campo próprio em ElegibilidadeRespostaCompleta
        # para estado pendente; registramos isso em erro_mensagem (técnico,
        # sem PII) para o chamador decidir se faz polling depois
        # (solicitacaoStatusAutorizacao — fora de escopo desta task).
        motivos_negativa = []
        erro_mensagem = ''
        if resultado.situacao == SituacaoAutorizacao.NEGADO:
            motivos_negativa = [{'codigo': resultado.codigo_glosa, 'descricao': resultado.descricao_glosa}]
        elif resultado.situacao == SituacaoAutorizacao.EM_ANALISE:
            erro_mensagem = 'orizon_autorizacao_em_analise'
        return ElegibilidadeRespostaCompleta(
            elegivel=(resultado.situacao == SituacaoAutorizacao.AUTORIZADO),
            numero_carteira=numero_carteira,
            beneficiario_nome=beneficiario_nome,
            origem=TISSElegibilidadeOrigem.AUTOMATICA,
            motivos_negativa=motivos_negativa,
            numero_guia_operadora=resultado.numero_guia_operadora,
            erro_mensagem=erro_mensagem,
        )

    # OrizonSOAPFaultResult — operadora rejeitou a PRÓPRIA solicitação
    # (ex.: login inválido), não uma resposta sobre o beneficiário.
    erro = f'{resultado.codigo_erro}: {resultado.descricao_erro}'
    _log_elegibilidade(clinic, operator_config, appointment_id, TISSElegibilidadeOrigem.AUTOMATICA, TISSElegibilidadeStatus.FALHA_OPERADORA, erro)
    return ElegibilidadeRespostaCompleta(
        elegivel=False, numero_carteira=numero_carteira, beneficiario_nome=beneficiario_nome,
        origem=TISSElegibilidadeOrigem.AUTOMATICA, erro_mensagem=erro,
    )


# Vocabulário de mock diferente entre os dois clients (ver docstring de
# _consultar_elegibilidade_orizon) — mapeado só para quem chama
# consultar_elegibilidade_automatica sem saber qual client vai ser usado
# internamente (ex.: testes/callers usando o vocabulário genérico
# 'success'/'negativa'/'error' também funcionam contra o path Orizon).
_ORIZON_MOCK_SCENARIO_MAP = {
    'success': 'autorizado',
    'negativa': 'negado',
    'error': 'fault',
}


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
