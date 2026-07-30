"""
Provider `generico_ans` — wrapper fino sobre `tiss/soap_client.py` +
`tiss/xml_builder.py` (padrão ANS publicado, versão 4.02.00).

**Este provider deixou de ser o default (D3).** Ele continua existindo
porque o código de transporte genérico ANS existe, é testado e é a base
sobre a qual uma operadora direta (SulAmérica/Porto/Mediservice) será
integrada. Mas selecioná-lo passou a ser uma AFIRMAÇÃO deliberada: "eu li o
manual técnico desta operadora e confirmei que ela aceita o dialeto genérico
do padrão ANS". Config que não faz essa afirmação fica em `desconhecido` e
falha explicitamente — ver `providers/desconhecido.py`.

Limitação conhecida e não resolvida aqui (buraco B4 do documento de
arquitetura): este caminho **não envia credencial nenhuma** — só
`Content-Type` e `SOAPAction`. `login_encrypted`/`senha_encrypted` hoje só
são lidos pelo builder da Orizon. Qualquer operadora direta vai falhar
autenticação por aqui até que o mecanismo dela seja implementado (WS-Security,
token, mTLS — depende da operadora). Por isso `capabilities().exige_credenciais`
é False: não é que a operadora não exija, é que este provider ainda não sabe
mandar. Está declarado assim para não mentir para a UI.
"""
import logging
from xml.sax.saxutils import escape

from ..models import TISSElegibilidadeOrigem, TISSElegibilidadeStatus
from ..soap_client import (
    enviar_lote as soap_enviar_lote,
    verificar_elegibilidade as soap_verificar_elegibilidade,
    SOAPClientError, SOAPSuccessResult, ElegibilidadeResult,
)
from ..xml_builder import build_lote_xml, XMLBuilderError, TISS_PADRAO_VERSAO
from ..xml_validator import validate_xml, XMLValidatorError
from .base import (
    CancelamentoResultado, ElegibilidadeRespostaCompleta, EnvioLoteResultado, OperacaoNaoSuportada,
    ProviderCapabilities, ProviderHealth,
)
from .health import wsdl_health_check

logger = logging.getLogger(__name__)


def _build_pedido_elegibilidade_xml(clinic, numero_carteira: str) -> str:
    """
    Monta o fragmento pedidoElegibilidade (ct_elegibilidadeVerifica) mínimo:
    dadosPrestador (choice — usamos cnpjContratado, sempre disponível via
    Clinic.cnpj) + numeroCarteira (únicos campos obrigatórios no XSD; os
    demais são todos minOccurs="0").

    BACFF-013: o "wrapping" exato da operação (se o corpo do SOAP deveria
    embrulhar isto dentro de pedidoElegibilidadeWS/cabecalho, e não só o
    fragmento cru) ainda não foi confirmado contra uma operadora real, só
    contra o padrão ANS publicado. É precisamente esta incerteza que motivou
    o D3: enquanto ela existir, nenhuma config deve cair aqui por omissão.
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


def verificar_cobertura(clinic, operator_config, numero_carteira,
                        beneficiario_nome='', appointment_id='',
                        mock_scenario='success') -> ElegibilidadeRespostaCompleta:
    """
    D1: no padrão ANS publicado a cobertura tem operação isolada
    (`tissVerificaElegibilidade_Operation`), então aqui a unificação
    elegibilidade+autorização é trivial — uma chamada só. O contrato é o
    mesmo do provider da Orizon, onde a mesma pergunta exige a operação de
    autorização. Quem chama não distingue os dois casos, que é o ponto.
    """
    xml_pedido = _build_pedido_elegibilidade_xml(clinic, numero_carteira)

    try:
        resultado = soap_verificar_elegibilidade(
            endpoint_url=operator_config.connection.endpoint_url,
            xml_mensagem_tiss=xml_pedido,
            mock_scenario=mock_scenario,
        )
    except SOAPClientError as exc:
        return ElegibilidadeRespostaCompleta(
            elegivel=False, numero_carteira=numero_carteira, beneficiario_nome=beneficiario_nome,
            origem=TISSElegibilidadeOrigem.AUTOMATICA, erro_mensagem=f'falha_soap: {exc}',
            status_operacional=TISSElegibilidadeStatus.FALHA_TRANSPORTE,
        )

    if isinstance(resultado, ElegibilidadeResult):
        return ElegibilidadeRespostaCompleta(
            elegivel=resultado.elegivel,
            numero_carteira=numero_carteira,
            beneficiario_nome=beneficiario_nome or resultado.nome_beneficiario,
            origem=TISSElegibilidadeOrigem.AUTOMATICA,
            motivos_negativa=[
                {'codigo': codigo, 'descricao': descricao}
                for codigo, descricao in resultado.motivos_negativa
            ],
            status_operacional=TISSElegibilidadeStatus.SUCESSO,
        )

    # SOAPFaultResult — operadora rejeitou a PRÓPRIA consulta (ex.: prestador
    # não autorizado), não uma resposta sobre o beneficiário.
    return ElegibilidadeRespostaCompleta(
        elegivel=False, numero_carteira=numero_carteira, beneficiario_nome=beneficiario_nome,
        origem=TISSElegibilidadeOrigem.AUTOMATICA,
        erro_mensagem=f'{resultado.codigo_erro}: {resultado.descricao_erro}',
        status_operacional=TISSElegibilidadeStatus.FALHA_OPERADORA,
    )


def enviar_lote(lote, guias, sequencial_transacao, mock_scenario='success') -> EnvioLoteResultado:
    """
    Monta o XML do lote no dialeto genérico ANS, valida contra o XSD e
    transporta. Não persiste nada — `services.enviar_lote` traduz este
    resultado em transições de status e `TISSGlosa`.
    """
    try:
        xml_completo, hash_md5 = build_lote_xml(
            lote=lote, guias=guias, clinic=lote.clinic,
            operator_config=lote.operator_config,
            sequencial_transacao=sequencial_transacao,
        )
    except XMLBuilderError as exc:
        return EnvioLoteResultado(
            sucesso=False, erro_code='xml_builder_failed', erro_mensagem=str(exc),
        )

    try:
        issues = validate_xml(xml_completo)
    except XMLValidatorError as exc:
        return EnvioLoteResultado(
            sucesso=False, erro_code='xsd_validator_unavailable', erro_mensagem=str(exc),
        )

    if issues:
        return EnvioLoteResultado(
            sucesso=False, erro_code='xml_schema_invalid',
            erro_mensagem='; '.join(str(i) for i in issues[:10]),
            xml_enviado=xml_completo, hash_epilogo=hash_md5,
        )

    try:
        resultado = soap_enviar_lote(
            endpoint_url=lote.operator_config.connection.endpoint_url,
            xml_mensagem_tiss=xml_completo,
            mock_scenario=mock_scenario,
        )
    except SOAPClientError as exc:
        return EnvioLoteResultado(
            sucesso=False, erro_code='soap_send_failed', erro_mensagem=str(exc),
            xml_enviado=xml_completo, hash_epilogo=hash_md5,
        )

    if isinstance(resultado, SOAPSuccessResult):
        return EnvioLoteResultado(
            sucesso=True, protocolo=resultado.protocolo, raw_response=resultado.raw_response,
            xml_enviado=xml_completo, hash_epilogo=hash_md5,
        )

    return EnvioLoteResultado(
        sucesso=False, erro_code='soap_fault',
        erro_mensagem=f'{resultado.codigo_erro}: {resultado.descricao_erro}',
        codigo_glosa=resultado.codigo_erro, descricao_glosa=resultado.descricao_erro,
        raw_response=resultado.raw_response,
        xml_enviado=xml_completo, hash_epilogo=hash_md5,
    )


def preparar_documento_assinatura(guia, clinic, operator_config, sequencial_transacao,
                                   documento_base64, nome_arquivo, tipo_documento='ANEXO'):
    """
    TASK-BO-10: `envioDocumentoWS` assinado (XMLDSig) não está implementado
    para o dialeto genérico ANS — só a Orizon confirma esta operação hoje.
    """
    raise OperacaoNaoSuportada(
        'Envio de documento assinado (XMLDSig) não está implementado para o provider genérico ANS.'
    )


def enviar_documento_assinado(xml_final, operator_config, mock_scenario='success'):
    raise OperacaoNaoSuportada(
        'Envio de documento assinado (XMLDSig) não está implementado para o provider genérico ANS.'
    )


def cancelar_guia(clinic, operator_config, guia, mock_scenario='success') -> CancelamentoResultado:
    """
    `tissCancelaGuia` existe no padrão ANS publicado, mas não há client de
    transporte implementado para esse dialeto ainda (ver docstring do
    módulo e `providers/base.py`, `envio_lote` tem o mesmo tipo de limitação
    documentada para o `soap_client.py` genérico). Falha alto e explícito —
    nunca fallback silencioso para o Autorize Orizon.
    """
    raise OperacaoNaoSuportada(
        'Cancelamento de guia no dialeto genérico ANS ainda não tem client implementado '
        '(ver tiss/soap_client.py). Cancelamento automático só está disponível hoje para o provider orizon.'
    )


def health_check(operator_config) -> ProviderHealth:
    return wsdl_health_check(operator_config.connection.endpoint_url, 'generico_ans')


def capabilities() -> ProviderCapabilities:
    return ProviderCapabilities(
        cobertura=True,
        envio_lote=True,
        # `tissSolicitacaoStatusProtocolo`/`tissCancelaGuia` existem no padrão
        # ANS, mas não há client implementado — declarar True aqui faria a UI
        # oferecer um botão que quebra.
        consulta_status=False,
        cancelamento_guia=False,
        versoes_padrao_suportadas=(TISS_PADRAO_VERSAO,),
        # Ver docstring do módulo (buraco B4): este caminho ainda não sabe
        # enviar credencial. Declarado False para não mentir para a UI.
        exige_credenciais=False,
        confirmado_em_homologacao=False,
    )
