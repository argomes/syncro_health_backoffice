"""
Provider `orizon` — Autorize da Orizon.

Fonte de verdade: `Autorize-Integracao-Tecnica-Webservice-TISS-4-01-00.pdf`
(manual técnico OFICIAL da Orizon). Transporte em
`tiss/orizon_autorize_client.py`, montagem de XML em
`tiss/orizon_autorize_xml_builder.py`. Este módulo é a cola: traduz a
pergunta de negócio do contrato para as operações que a Orizon expõe e
normaliza o retorno.

**Por que a unificação do D1 cai bem aqui.** A Orizon não tem
`tissVerificaElegibilidade` isolada: a cobertura do beneficiário é
verificada DENTRO do próprio pedido de autorização
(`solicitacaoProcedimentoWS/solicitacaoSP-SADT`, resposta em
`autorizacaoProcedimentoWS`). Sob o contrato antigo (duas funções), a UI
teria que perguntar "esta operadora tem elegibilidade isolada?" e mudar de
fluxo — hardcode que acabaria virando `if operadora == 'orizon'` no
frontend, no parque de clínicas, onde é caríssimo desfazer. Com
`verificar_cobertura` unificada, o chamador faz a mesma chamada para
qualquer operadora e este módulo resolve para o que a Orizon realmente
oferece.
"""
import logging
import uuid
from datetime import datetime

from ..models import TISSElegibilidadeOrigem, TISSElegibilidadeStatus, TISSGuia
from ..orizon_autorize_client import (
    solicitar_autorizacao as orizon_solicitar_autorizacao,
    cancelar_guia as orizon_cancelar_guia,
    OrizonAutorizeClientError, AutorizacaoResult, CancelamentoResult, SituacaoAutorizacao,
    SituacaoCancelamento,
)
from ..orizon_autorize_xml_builder import (
    build_solicitacao_procedimento_xml, build_cancelamento_guia_xml, OrizonAutorizeXMLBuilderError,
    get_tiss_padrao_versao_orizon,
)
from .base import (
    CancelamentoResultado, ElegibilidadeRespostaCompleta, EnvioLoteResultado, OperacaoNaoSuportada,
    ProviderCapabilities, ProviderHealth,
)
from .health import wsdl_health_check

logger = logging.getLogger(__name__)

# Vocabulário de mock diferente entre os clients: o genérico usa
# 'success'/'negativa'/'error', o Autorize usa
# 'autorizado'/'negado'/'em_analise'/'fault'. O mapa fica aqui (no provider,
# não em services) exatamente para que quem chama o contrato não precise
# saber qual client roda por baixo — inclusive testes e callers que já usam
# o vocabulário genérico continuam funcionando contra o caminho Orizon.
_MOCK_SCENARIO_MAP = {
    'success': 'autorizado',
    'negativa': 'negado',
    'error': 'fault',
}


def verificar_cobertura(clinic, operator_config, numero_carteira,
                        beneficiario_nome='', appointment_id='',
                        mock_scenario='success') -> ElegibilidadeRespostaCompleta:
    """
    Este endpoint não recebe uma guia real (nem procedimentos) — só
    numero_carteira/appointment_id — então montamos um objeto guia mínimo
    NÃO PERSISTIDO só para reaproveitar `build_solicitacao_procedimento_xml`
    sem duplicar a montagem de XML. O `TISSGuia(...)` abaixo nunca chega a
    `.save()`: é um value object transitório, não um registro órfão.
    """
    # BACFF-014 (achado 3, 2026-07-29): 'ELEGIBILIDADE' era um literal fixo
    # reutilizado como numeroGuiaPrestador em toda chamada sem appointment_id
    # — o manual confirma que a Orizon nega automaticamente (Bradesco) uma
    # transação que reutiliza o mesmo número de guia. Gera um identificador
    # único por chamada (uuid4) para nunca repetir entre consultas avulsas.
    # TISSGuia.numero tem max_length=20 (numeroGuiaPrestador) — usa o hex do
    # uuid4 truncado para caber, sem prefixo, mantendo unicidade prática.
    numero_guia_avulsa = uuid.uuid4().hex[:20]
    guia_transiente = TISSGuia(
        clinic=clinic,
        appointment_id=appointment_id,
        numero=appointment_id or numero_guia_avulsa,
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
        return ElegibilidadeRespostaCompleta(
            elegivel=False, numero_carteira=numero_carteira, beneficiario_nome=beneficiario_nome,
            origem=TISSElegibilidadeOrigem.AUTOMATICA,
            erro_mensagem=f'falha_montagem_xml_orizon: {exc}',
            status_operacional=TISSElegibilidadeStatus.FALHA_TRANSPORTE,
        )

    try:
        resultado = orizon_solicitar_autorizacao(
            endpoint_url=operator_config.connection.endpoint_url,
            xml_solicitacao=xml_solicitacao,
            mock_scenario=_MOCK_SCENARIO_MAP.get(mock_scenario, mock_scenario),
        )
    except OrizonAutorizeClientError as exc:
        return ElegibilidadeRespostaCompleta(
            elegivel=False, numero_carteira=numero_carteira, beneficiario_nome=beneficiario_nome,
            origem=TISSElegibilidadeOrigem.AUTOMATICA,
            erro_mensagem=f'falha_soap_orizon: {exc}',
            status_operacional=TISSElegibilidadeStatus.FALHA_TRANSPORTE,
        )

    if isinstance(resultado, AutorizacaoResult):
        # A Orizon não devolve nome do beneficiário nesta operação (ao
        # contrário do client genérico) — beneficiario_nome só reflete o que
        # já veio do chamador. `situacao='em_analise'` não é negativa nem tem
        # campo próprio para estado pendente; registramos em erro_mensagem
        # (técnico, sem PII) para o chamador decidir se faz polling depois
        # (solicitacaoStatusAutorizacao — ver capabilities().consulta_status).
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
            status_operacional=TISSElegibilidadeStatus.SUCESSO,
        )

    # SOAPFaultResult (módulo Orizon) — operadora rejeitou a PRÓPRIA
    # solicitação (ex.: login inválido), não uma resposta sobre o beneficiário.
    return ElegibilidadeRespostaCompleta(
        elegivel=False, numero_carteira=numero_carteira, beneficiario_nome=beneficiario_nome,
        origem=TISSElegibilidadeOrigem.AUTOMATICA,
        erro_mensagem=f'{resultado.codigo_erro}: {resultado.descricao_erro}',
        status_operacional=TISSElegibilidadeStatus.FALHA_OPERADORA,
    )


def enviar_lote(lote, guias, sequencial_transacao, mock_scenario='success') -> EnvioLoteResultado:
    """
    D4: o envio de lote da Orizon é o **Fature**, um webservice distinto do
    Autorize, com client próprio (`orizon_fature_client.py`) ainda não
    escrito — é P0 separado do BACFF-014, deliberadamente sequenciado DEPOIS
    desta arquitetura para não mover `enviar_lote` duas vezes.

    Até lá, falha alto e explicitamente. Nunca fazer fallback silencioso
    para o dialeto genérico ANS: mandar o lote de uma clínica Orizon para o
    endpoint/envelope errado gera glosa e retrabalho de faturamento semanas
    depois — muito pior que um erro visível agora. Este é exatamente o bug
    B2 que o hotfix BACFF-AVULSA-13 (PR #48) corrigiu como paliativo em
    `services.py`; aqui ele passa a ser uma propriedade declarada do
    provider, no lugar certo.
    """
    raise OperacaoNaoSuportada(
        'Envio de lote para a Orizon depende do webservice Fature, ainda não implementado '
        '(orizon_fature_client.py). A elegibilidade/autorização via Autorize continua funcionando.'
    )


_MOCK_SCENARIO_MAP_CANCELAMENTO = {
    'success': 'cancelado',
    'negativa': 'nao_cancelado',
    'error': 'fault',
}


def cancelar_guia(clinic, operator_config, guia, mock_scenario='success') -> CancelamentoResultado:
    """
    BACFF-014 (2026-07-30): cancela junto à Orizon uma guia SP-SADT já
    autorizada previamente (`cancelaGuiaWS` — ver
    `orizon_autorize_xml_builder.build_cancelamento_guia_xml`, schema
    confirmado contra o manual Autorize 4.03.00, página 30-31). Disparado
    pela task Celery `tiss/tasks.py::cancelar_guia_orizon_task` quando o
    atendimento correspondente é cancelado do lado da clínica.

    Nunca levanta exceção de rede/transporte — falha vira
    `CancelamentoResultado(sucesso=False, erro_code=..., erro_mensagem=...)`
    para a task decidir se faz retry (mesmo padrão de `enviar_lote`).
    """
    sequencial_transacao = datetime.now().strftime('%y%m%d%H%M%S')

    try:
        xml_cancelamento, hash_md5 = build_cancelamento_guia_xml(
            guia=guia, clinic=clinic, operator_config=operator_config,
            sequencial_transacao=sequencial_transacao,
        )
    except OrizonAutorizeXMLBuilderError as exc:
        return CancelamentoResultado(
            sucesso=False, erro_code='xml_builder_failed',
            erro_mensagem=f'falha_montagem_xml_cancelamento_orizon: {exc}',
        )

    try:
        resultado = orizon_cancelar_guia(
            endpoint_url=operator_config.connection.endpoint_url,
            xml_cancelamento=xml_cancelamento,
            mock_scenario=_MOCK_SCENARIO_MAP_CANCELAMENTO.get(mock_scenario, mock_scenario),
        )
    except OrizonAutorizeClientError as exc:
        return CancelamentoResultado(
            sucesso=False, erro_code='soap_network_error',
            erro_mensagem=f'falha_soap_orizon: {exc}',
        )

    if isinstance(resultado, CancelamentoResult):
        return CancelamentoResultado(
            sucesso=(resultado.situacao == SituacaoCancelamento.CANCELADO),
            numero_guia_operadora=resultado.numero_guia_operadora,
            raw_response=resultado.raw_response,
            erro_code='' if resultado.situacao == SituacaoCancelamento.CANCELADO else 'guia_nao_cancelada',
            erro_mensagem='' if resultado.situacao == SituacaoCancelamento.CANCELADO else 'orizon_recusou_cancelamento',
        )

    # SOAPFaultResult — operadora rejeitou a PRÓPRIA solicitação de
    # cancelamento (ex.: login inválido, guia não encontrada).
    return CancelamentoResultado(
        sucesso=False, raw_response=resultado.raw_response,
        erro_code='soap_fault',
        erro_mensagem=f'{resultado.codigo_erro}: {resultado.descricao_erro}',
    )


def health_check(operator_config) -> ProviderHealth:
    return wsdl_health_check(operator_config.connection.endpoint_url, 'orizon')


def capabilities() -> ProviderCapabilities:
    return ProviderCapabilities(
        # D1: cobertura unificada. Na Orizon ela é atendida pela operação de
        # autorização (solicitacaoProcedimentoWS), não por elegibilidade
        # isolada — detalhe que o contrato esconde de propósito.
        cobertura=True,
        # Fature ainda não implementado (D4).
        envio_lote=False,
        # O manual documenta solicitacaoStatusAutorizacao para o estado "Em
        # Análise" (polling >= 30min). O client ainda não implementa a
        # operação, então False: capability declara o que ESTE código faz,
        # não o que a operadora oferece — senão a UI oferece botão que quebra.
        consulta_status=False,
        # BACFF-014 (2026-07-30): cancelar_guia implementado (cancelaGuiaWS,
        # schema confirmado contra o manual — ver providers/orizon.py). True
        # aqui reflete o que o CÓDIGO faz; `confirmado_em_homologacao`
        # abaixo segue False até validação real, mesma distinção já usada
        # para `cobertura`.
        cancelamento_guia=True,
        versoes_padrao_suportadas=(get_tiss_padrao_versao_orizon(),),
        exige_credenciais=True,
        # Bloqueado desde o BACFF-014: homologação exige clínica-piloto
        # credenciada (a SyncroHealth é fornecedora, não prestadora). O
        # parser de `autorizacaoProcedimento` segue o padrão TISS genérico e
        # ainda não foi confirmado contra resposta real.
        confirmado_em_homologacao=False,
    )
