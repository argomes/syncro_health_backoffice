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
from datetime import datetime

from ..models import TISSElegibilidadeOrigem, TISSElegibilidadeStatus, TISSGuia
from ..orizon_autorize_client import (
    solicitar_autorizacao as orizon_solicitar_autorizacao,
    OrizonAutorizeClientError, AutorizacaoResult, SituacaoAutorizacao,
)
from ..orizon_autorize_xml_builder import (
    build_solicitacao_procedimento_xml, OrizonAutorizeXMLBuilderError,
    TISS_PADRAO_VERSAO_ORIZON,
)
from .base import (
    ElegibilidadeRespostaCompleta, EnvioLoteResultado, OperacaoNaoSuportada,
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
        return ElegibilidadeRespostaCompleta(
            elegivel=False, numero_carteira=numero_carteira, beneficiario_nome=beneficiario_nome,
            origem=TISSElegibilidadeOrigem.AUTOMATICA,
            erro_mensagem=f'falha_montagem_xml_orizon: {exc}',
            status_operacional=TISSElegibilidadeStatus.FALHA_TRANSPORTE,
        )

    try:
        resultado = orizon_solicitar_autorizacao(
            endpoint_url=operator_config.endpoint_url,
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


def health_check(operator_config) -> ProviderHealth:
    return wsdl_health_check(operator_config.endpoint_url, 'orizon')


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
        cancelamento_guia=False,
        versoes_padrao_suportadas=(TISS_PADRAO_VERSAO_ORIZON,),
        exige_credenciais=True,
        # Bloqueado desde o BACFF-014: homologação exige clínica-piloto
        # credenciada (a SyncroHealth é fornecedora, não prestadora). O
        # parser de `autorizacaoProcedimento` segue o padrão TISS genérico e
        # ainda não foi confirmado contra resposta real.
        confirmado_em_homologacao=False,
    )
