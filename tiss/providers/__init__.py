"""
Registro e resolução de providers de operadora TISS — **o único switch do
sistema** (§4 do documento de arquitetura).

Antes desta camada havia um `if provider == ORIZON` por operação, espalhado
em `services.py`. Isso já tinha produzido um bug real em produção (B2 /
issue #46: `enviar_lote` chamava o client genérico incondicionalmente,
mandando lote de clínica Orizon para o endpoint errado). A regra que este
módulo impõe: **nenhum outro arquivo do sistema decide "qual código roda
para qual operadora"**. Uma operação nova que precise despachar por
operadora ganha uma função no contrato (`providers/base.py`), não um `if`
novo em `services.py`.

O conjunto de providers é conhecido em tempo de deploy — um `dict` é a
estrutura correta e final. Explicitamente NÃO fazemos: `entry_points`,
plugin dinâmico, carregamento por string, hot-reload, nem ABC com N métodos
abstratos. O que garante a conformidade dos providers é a suíte de testes
de contrato parametrizada sobre `_PROVIDERS` (`tests_providers.py`), não
cerimônia de tipos — ver §6 do documento para o gatilho de formalização
(3 providers, ou um dev externo escrevendo um).
"""
import logging
import time

from ..models import (
    TISSGatewayProvider, OperatorCallLog, OperatorCallOperation, OperatorCallOutcome,
    TISSElegibilidadeStatus,
)
from . import desconhecido, generico_ans, orizon
from .base import (  # noqa: F401 — reexport: o contrato é importado daqui
    ElegibilidadeRespostaCompleta, EnvioLoteResultado, ProviderCapabilities, ProviderHealth,
    ProviderError, OperadoraDesativada, ProviderNaoRegistrado, ProviderNaoConfirmado,
    OperacaoNaoSuportada,
)

logger = logging.getLogger(__name__)


# Toda entrada de `TISSGatewayProvider` DEVE aparecer aqui. Há teste de
# integridade (§8.6) que quebra o build se alguém adicionar um valor ao
# TextChoices sem registrar o provider correspondente — a falha aparece no
# CI, não numa consulta de paciente real na recepção.
_PROVIDERS = {
    TISSGatewayProvider.DESCONHECIDO: desconhecido,
    TISSGatewayProvider.GENERICO_ANS: generico_ans,
    TISSGatewayProvider.ORIZON: orizon,
}


def provider_module(gateway_provider):
    """
    Resolve o MÓDULO do provider sem checar `ativo`.

    Existe separado de `resolve()` porque `capabilities()` e `health_check()`
    precisam funcionar com a operadora DESATIVADA: a UI ainda quer saber o
    que aquele provider suportaria, e o admin ainda quer poder testar a
    conexão antes de religar a integração. O que `ativo=False` bloqueia é o
    caminho de SAÍDA de negócio, não a introspecção.
    """
    try:
        return _PROVIDERS[gateway_provider]
    except KeyError:
        # "Desplugar" de Nível 2 (§4.2): provider removido do código com
        # config ainda apontando para ele. Falha alto — nunca fallback.
        raise ProviderNaoRegistrado(
            f'gateway_provider "{gateway_provider}" não está registrado em _PROVIDERS'
        ) from None


def resolve(operator_config):
    """
    Ponto ÚNICO de resolução para chamadas de negócio à operadora.

    Faz duas coisas, nesta ordem:
    1. "Desplugar" de Nível 1 (§4.2): se `operator_config.ativo is False`,
       levanta `OperadoraDesativada` ANTES de qualquer I/O de rede. Este é o
       bug B1/issue #45 — o campo `ativo` existia (com índice e tudo) e
       nenhum caminho de código o lia; desativar uma operadora no admin não
       desativava nada. O hotfix BACFF-AVULSA-12 (PR #48) resolveu isso com
       `services._checar_operadora_ativa`; aqui a checagem passa a viver no
       ponto de despacho, onde é impossível uma operação nova esquecer dela.
    2. Devolve o provider já INSTRUMENTADO (§4.4(a)): latência e outcome de
       toda chamada de negócio vão para `OperatorCallLog` sem que o provider
       escreva uma linha de instrumentação. É o que torna o health check
       genérico de verdade, em vez de "genérico se o dev lembrar".

    D2: o registro manual (`services.registrar_elegibilidade_manual`) NÃO
    passa por aqui de propósito — não faz I/O com a operadora e deve
    continuar funcionando com a integração desligada, para a clínica
    degradar para o fluxo manual em vez de parar.
    """
    if not operator_config.ativo:
        raise OperadoraDesativada(
            f'Operadora {operator_config.registro_ans} está desativada para esta clínica'
        )
    return _InstrumentedProvider(provider_module(operator_config.gateway_provider), operator_config)


def capabilities_for(operator_config) -> ProviderCapabilities:
    """Atalho para o serializer/admin — funciona mesmo com a operadora desativada."""
    return provider_module(operator_config.gateway_provider).capabilities()


def health_check(operator_config) -> ProviderHealth:
    """Atalho para o botão "Testar conexão" — funciona mesmo com a operadora desativada."""
    return provider_module(operator_config.gateway_provider).health_check(operator_config)


def _registrar_chamada(operator_config, operation, outcome, latency_ms):
    """
    Escrita do log passivo. Um único INSERT, síncrono de propósito: o custo é
    irrelevante perto de uma chamada SOAP e enfileirar (Celery) só
    adicionaria um modo de falha.

    Envolvido em try/except porque observabilidade NUNCA pode derrubar a
    chamada de negócio: se a tabela de log estiver indisponível, a
    recepcionista ainda precisa da resposta da operadora.
    """
    try:
        OperatorCallLog.objects.create(
            registro_ans=operator_config.registro_ans,
            gateway_provider=operator_config.gateway_provider,
            operation=operation,
            clinic=operator_config.clinic,
            outcome=outcome,
            latency_ms=latency_ms,
        )
    except Exception:  # noqa: BLE001 — ver docstring
        logger.warning('tiss.providers: falha ao registrar OperatorCallLog (chamada de negócio não afetada)')


_OUTCOME_POR_STATUS_ELEGIBILIDADE = {
    TISSElegibilidadeStatus.SUCESSO: OperatorCallOutcome.SUCCESS,
    TISSElegibilidadeStatus.FALHA_TRANSPORTE: OperatorCallOutcome.NETWORK_ERROR,
    TISSElegibilidadeStatus.FALHA_OPERADORA: OperatorCallOutcome.SOAP_FAULT,
}

_OUTCOME_POR_ERRO_LOTE = {
    'soap_send_failed': OperatorCallOutcome.NETWORK_ERROR,
    'soap_fault': OperatorCallOutcome.SOAP_FAULT,
}


class _InstrumentedProvider:
    """
    Proxy fino sobre o módulo do provider (§4.4(a)).

    Só instrumenta as duas operações que fazem I/O de negócio com a
    operadora (`verificar_cobertura`, `enviar_lote`). `capabilities()` é
    estático e `health_check()` é uma sonda explícita do admin — nenhum dos
    dois representa tráfego real da clínica, e contá-los poluiria a
    classificação de saúde (uma operadora "saudável" só porque alguém
    apertou "Testar conexão" seria pior que informação nenhuma).
    """

    def __init__(self, modulo, operator_config):
        self._modulo = modulo
        self._operator_config = operator_config

    @property
    def nome(self) -> str:
        return self._modulo.__name__.rsplit('.', 1)[-1]

    def verificar_cobertura(self, clinic, operator_config, numero_carteira,
                            beneficiario_nome='', appointment_id='',
                            mock_scenario='success') -> ElegibilidadeRespostaCompleta:
        inicio = time.perf_counter()
        try:
            resultado = self._modulo.verificar_cobertura(
                clinic, operator_config, numero_carteira,
                beneficiario_nome=beneficiario_nome, appointment_id=appointment_id,
                mock_scenario=mock_scenario,
            )
        except ProviderError:
            _registrar_chamada(
                operator_config, OperatorCallOperation.COBERTURA,
                OperatorCallOutcome.PROVIDER_ERROR, int((time.perf_counter() - inicio) * 1000),
            )
            raise

        _registrar_chamada(
            operator_config, OperatorCallOperation.COBERTURA,
            _OUTCOME_POR_STATUS_ELEGIBILIDADE.get(
                resultado.status_operacional, OperatorCallOutcome.SUCCESS,
            ),
            int((time.perf_counter() - inicio) * 1000),
        )
        return resultado

    def enviar_lote(self, lote, guias, sequencial_transacao, mock_scenario='success') -> EnvioLoteResultado:
        inicio = time.perf_counter()
        try:
            resultado = self._modulo.enviar_lote(lote, guias, sequencial_transacao, mock_scenario=mock_scenario)
        except ProviderError:
            _registrar_chamada(
                self._operator_config, OperatorCallOperation.ENVIO_LOTE,
                OperatorCallOutcome.PROVIDER_ERROR, int((time.perf_counter() - inicio) * 1000),
            )
            raise

        if resultado.sucesso:
            outcome = OperatorCallOutcome.SUCCESS
        else:
            # Falhas locais (XML/XSD) não são falha DA OPERADORA — não devem
            # contaminar a métrica de saúde dela. Ficam como provider_error.
            outcome = _OUTCOME_POR_ERRO_LOTE.get(resultado.erro_code, OperatorCallOutcome.PROVIDER_ERROR)
        _registrar_chamada(
            self._operator_config, OperatorCallOperation.ENVIO_LOTE, outcome,
            int((time.perf_counter() - inicio) * 1000),
        )
        return resultado

    def health_check(self) -> ProviderHealth:
        return self._modulo.health_check(self._operator_config)

    def capabilities(self) -> ProviderCapabilities:
        return self._modulo.capabilities()

    def preparar_documento_assinatura(self, guia, clinic, sequencial_transacao,
                                       documento_base64, nome_arquivo, tipo_documento='ANEXO'):
        """
        TASK-BO-10 — passthrough SEM instrumentação de `OperatorCallLog`: não
        é uma chamada de negócio à operadora (é montagem/canonicalização
        local), então não deve poluir a métrica de saúde da operadora — mesmo
        raciocínio de `capabilities()`/`health_check()` acima.
        """
        return self._modulo.preparar_documento_assinatura(
            guia, clinic, self._operator_config, sequencial_transacao,
            documento_base64, nome_arquivo, tipo_documento=tipo_documento,
        )

    def enviar_documento_assinado(self, xml_final, mock_scenario='success'):
        """
        TASK-BO-10 — esta SIM é uma chamada de negócio real à operadora, mas
        fica deliberadamente FORA de `OperatorCallOperation`/`OperatorCallLog`
        nesta rodada (escopo desta task é o handoff de assinatura, não o
        dashboard de saúde — ver `.claude/tasks` para o item de extensão
        futura, se o volume de envioDocumentoWS justificar).
        """
        return self._modulo.enviar_documento_assinado(
            xml_final, self._operator_config, mock_scenario=mock_scenario,
        )
