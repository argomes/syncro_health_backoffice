"""
Provider `desconhecido` — D3 (decisão do Tech Lead, 2026-07-28).

Este provider existe para FALHAR, e é o default do model.

Contexto do porquê. Até esta arquitetura, `TISSOperatorConfig.gateway_provider`
tinha `generico_ans` como default. Ou seja: toda config criada sem escolha
explícita — pelo admin, por fixture, por seed, pela API — assumia
silenciosamente que a operadora fala o dialeto genérico do padrão ANS
publicado. Isso nunca foi verificado contra operadora nenhuma. Pior: o
client genérico (`soap_client.py`) sequer envia credencial (buraco B4 do
documento de arquitetura), então o "sucesso" desse caminho contra uma
operadora real seria, na melhor das hipóteses, um erro de autenticação — e
na pior, um payload aceito no dialeto errado, que vira glosa e retrabalho
de faturamento na clínica semanas depois.

A regra que este módulo materializa: **um dialeto não confirmado nunca é
tentado.** Chamada automática bloqueia, alto e cedo, com um código de erro
que diz exatamente o que falta fazer (confirmar o manual técnico da
operadora e escolher o provider). O caminho manual da recepção continua
aberto — ver D2 e `services.registrar_elegibilidade_manual` — então a
clínica degrada em vez de parar.

Sair deste estado é uma ação humana deliberada: alguém leu o manual
oficial da operadora, confirmou versão do padrão/envelope/auth e trocou o
`gateway_provider` para um provider que implementa aquele dialeto.
"""
from .base import (
    ProviderCapabilities, ProviderHealth, ProviderNaoConfirmado,
)

_MENSAGEM = (
    'Operadora sem dialeto TISS confirmado (gateway_provider="desconhecido"). '
    'Confirme o manual técnico oficial da operadora e selecione o provider '
    'correspondente antes de habilitar a integração automática. '
    'O registro manual de elegibilidade permanece disponível.'
)


def verificar_cobertura(clinic, operator_config, numero_carteira,
                        beneficiario_nome='', appointment_id='',
                        mock_scenario='success'):
    raise ProviderNaoConfirmado(_MENSAGEM)


def enviar_lote(lote, guias, sequencial_transacao, mock_scenario='success'):
    raise ProviderNaoConfirmado(_MENSAGEM)


def health_check(operator_config) -> ProviderHealth:
    """
    Não sonda nada: não sabemos sequer se este endpoint é SOAP. Devolver
    `reachable=False` com detalhe explícito é mais honesto (e mais útil no
    admin) do que um GET especulativo que pode dar 200 num endpoint que não
    tem nada a ver com TISS.
    """
    return ProviderHealth(reachable=False, latency_ms=0, detail='provider_nao_confirmado')


def capabilities() -> ProviderCapabilities:
    """Tudo False, de propósito: a UI não deve oferecer nenhum botão automático."""
    return ProviderCapabilities()
