"""
BO-08.3 — Orquestra as operações TISS de negócio: criação/envio de lote e
consulta de cobertura (elegibilidade/autorização).

**Este módulo não sabe mais falar com operadora nenhuma.** Todo dialeto
(envelope, autenticação, versão do padrão, montagem de XML, transporte)
vive em `tiss/providers/`, resolvido por `providers.resolve()` — o único
switch do sistema. O que sobra aqui é o que é genuinamente comum a qualquer
operadora: persistência (`TISSLote`, `TISSGuia`, `TISSGlosa`), log
operacional LGPD-safe (`TISSElegibilidadeConsulta`) e tradução de erro de
provider para o contrato de erro da API REST.

Consequência prática, e o teste de aceite do desenho (§5 do documento de
arquitetura): plugar uma operadora nova **não deve exigir tocar neste
arquivo**. Se exigir, o desenho vazou.
"""
import logging

from django.db import transaction

from .models import (
    TISSLote, TISSLoteStatus, TISSGuia, TISSGuiaStatus, TISSGlosa,
    TISSElegibilidadeConsulta, TISSElegibilidadeOrigem, TISSElegibilidadeStatus,
)
from . import providers
from .providers.base import (  # noqa: F401 — reexport histórico (ver nota abaixo)
    ElegibilidadeRespostaCompleta,
)

logger = logging.getLogger(__name__)

# `ElegibilidadeRespostaCompleta` era definida aqui até a arquitetura de
# providers. Passou para `providers/base.py` (é o tipo de retorno do
# contrato, e services agora depende de providers, não o contrário) e
# continua importável daqui para não quebrar chamadores existentes.


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
    Fluxo completo: resolve o provider da operadora, busca as guias do lote
    (ou as informadas), delega montagem/validação/transporte ao provider e
    persiste o resultado, criando `TISSGlosa` se houver rejeição.

    Sempre isolado à clínica do próprio lote (guias vêm de `lote.guias` ou de
    `TISSGuia.objects.filter(clinic=lote.clinic, ...)` — nunca de outra clínica).

    A resolução do provider acontece ANTES de tocar em qualquer estado do
    lote: operadora desativada ou provider não confirmado devem bloquear sem
    deixar o lote num status intermediário enganoso.
    """
    provider = _resolver_provider(lote.operator_config)

    guias = list(lote.guias.all()) if guia_ids is None else list(
        TISSGuia.objects.filter(clinic=lote.clinic, id__in=guia_ids)
    )
    if not guias:
        raise TISSServiceError('lote_sem_guias', 'Lote não possui guias associadas')

    sequencial_transacao = f'{lote.numero_lote:012d}'

    try:
        resultado = provider.enviar_lote(lote, guias, sequencial_transacao, mock_scenario=mock_scenario)
    except providers.OperacaoNaoSuportada as exc:
        # Ex.: Orizon + envio de lote (depende do Fature, D4). O lote fica
        # marcado com erro para o operador ver o motivo no admin, mas nenhum
        # payload foi enviado no dialeto errado — que é o ponto.
        #
        # O código de erro `provider_lote_nao_implementado` é preservado do
        # hotfix BACFF-AVULSA-13 (PR #48) DE PROPÓSITO: o gateway já trata
        # essa string, e trocá-la seria uma quebra de contrato de API sem
        # ganho nenhum.
        return _falhar_lote(
            lote, 'provider_lote_nao_implementado',
            f'provider_lote_nao_implementado: {exc}',
        )
    except providers.ProviderError as exc:
        return _falhar_lote(lote, exc.code, f'{exc.code}: {exc}')

    if resultado.sucesso:
        lote.status = TISSLoteStatus.ENVIADO
        lote.protocolo = resultado.protocolo
        lote.xml_enviado = resultado.xml_enviado
        lote.hash_epilogo = resultado.hash_epilogo
        lote.xml_recebido = resultado.raw_response
        lote.save(update_fields=[
            'status', 'protocolo', 'xml_enviado', 'hash_epilogo', 'xml_recebido', 'updated_at',
        ])
        TISSGuia.objects.filter(id__in=[g.id for g in guias]).update(status=TISSGuiaStatus.ENVIADA, lote=lote)
        return lote

    lote.status = TISSLoteStatus.ERRO_ENVIO
    lote.erro_mensagem = f'{resultado.erro_code}: {resultado.erro_mensagem}'
    lote.xml_enviado = resultado.xml_enviado
    lote.hash_epilogo = resultado.hash_epilogo
    lote.xml_recebido = resultado.raw_response
    lote.save(update_fields=[
        'status', 'erro_mensagem', 'xml_enviado', 'hash_epilogo', 'xml_recebido', 'updated_at',
    ])

    if resultado.erro_code == 'soap_fault':
        # Rejeição de NEGÓCIO pela operadora: cada guia do lote vira glosa.
        for guia in guias:
            guia.status = TISSGuiaStatus.GLOSADA
            guia.lote = lote
            guia.save(update_fields=['status', 'lote', 'updated_at'])
            TISSGlosa.objects.create(
                guia=guia,
                codigo=resultado.codigo_glosa,
                descricao=resultado.descricao_glosa,
                valor_glosado=guia.valor,
            )

    raise TISSServiceError(resultado.erro_code, resultado.erro_mensagem)


def _falhar_lote(lote: TISSLote, code: str, mensagem: str):
    lote.status = TISSLoteStatus.ERRO_ENVIO
    lote.erro_mensagem = mensagem
    lote.save(update_fields=['status', 'erro_mensagem', 'updated_at'])
    raise TISSServiceError(code, mensagem)


def _resolver_provider(operator_config):
    """
    Ponte entre as exceções do contrato de provider e o contrato de erro da
    API REST. `ProviderError.code` já é a string estável consumida pelo
    gateway Go; só reembrulhamos em `TISSServiceError` porque é o tipo que
    as views já capturam.
    """
    try:
        return providers.resolve(operator_config)
    except providers.ProviderError as exc:
        raise TISSServiceError(exc.code, str(exc)) from exc


def _log_elegibilidade(clinic, operator_config, appointment_id, origem, status, erro_mensagem=''):
    """
    Persiste SÓ o log operacional (BACFF-AVULSA-01) — sem numero_carteira,
    beneficiario_nome, elegivel, motivos_negativa nem XML. `erro_mensagem`
    aqui é sempre técnica (código/descrição de falha de transporte ou fault
    da operadora), nunca conteúdo do beneficiário — os providers, que montam
    essa string, são responsáveis por essa garantia (há teste anti-vazamento
    parametrizado sobre todos eles em `tests_providers.py`).
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
    Consulta síncrona de COBERTURA do beneficiário.

    D1 (Tech Lead, 2026-07-28): elegibilidade e autorização são um conceito
    só no contrato do provider. Esta função continua com o nome histórico
    (`consultar_elegibilidade_automatica`) porque é o nome que views, gateway
    Go e testes já usam — renomear seria uma quebra de contrato sem ganho.
    Mas o que ela chama é `provider.verificar_cobertura`, e o provider
    resolve para a(s) operação(ões) SOAP que a operadora dele oferece:
    `tissVerificaElegibilidade` no genérico ANS, `solicitacaoProcedimentoWS`
    na Orizon. Este módulo não sabe nem precisa saber qual foi.

    BACFF-AVULSA-01: só persiste um log OPERACIONAL (sucesso/falha) no banco
    central — o conteúdo clínico completo (elegível, motivos, nome do
    beneficiário) só existe na resposta devolvida daqui, nunca no banco
    central. Quem precisa do histórico completo persiste localmente (Edge
    Gateway).
    """
    provider = _resolver_provider(operator_config)

    try:
        resultado = provider.verificar_cobertura(
            clinic, operator_config, numero_carteira,
            beneficiario_nome=beneficiario_nome, appointment_id=appointment_id,
            mock_scenario=mock_scenario,
        )
    except providers.ProviderError as exc:
        raise TISSServiceError(exc.code, str(exc)) from exc

    _log_elegibilidade(
        clinic, operator_config, appointment_id, TISSElegibilidadeOrigem.AUTOMATICA,
        resultado.status_operacional, resultado.erro_mensagem
        if resultado.status_operacional != TISSElegibilidadeStatus.SUCESSO else '',
    )
    return resultado


def registrar_elegibilidade_manual(
    clinic, operator_config, numero_carteira: str, numero_guia_operadora: str,
    elegivel: bool, beneficiario_nome: str = '', appointment_id: str = '',
) -> ElegibilidadeRespostaCompleta:
    """
    BACFF-013: fallback de primeira classe, não uma exceção informal — a
    recepcionista ligou/acessou o portal da operadora diretamente e digitou
    o número da guia gerada manualmente. numero_guia_operadora é obrigatório.

    **D2 (Tech Lead, 2026-07-28): não passa por `providers.resolve()` de
    propósito.** Este caminho não faz I/O de rede com a operadora — a
    informação já foi obtida por telefone/portal — e deve continuar
    funcionando com a operadora DESATIVADA e com o provider em
    `desconhecido`. É essa propriedade que torna "desplugar" seguro do ponto
    de vista de continuidade operacional: a clínica degrada para o fluxo
    manual em vez de parar.

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


_STATUS_GUIA_COM_AUTORIZACAO_EMITIDA = frozenset({
    TISSGuiaStatus.ENVIADA, TISSGuiaStatus.ACEITA, TISSGuiaStatus.PARCIAL, TISSGuiaStatus.GLOSADA,
})


def disparar_cancelamento_guia(guia: TISSGuia) -> bool:
    """
    BACFF-014 (2026-07-30) — gatilho do cancelamento automático de guia.
    Chamado quando o atendimento/agendamento correspondente é cancelado do
    lado da clínica (ver `tiss/views.py::cancelar_guia_view`, consumido pelo
    Edge Gateway autenticado por license_key).

    Só enfileira a task Celery se a guia já teve alguma autorização emitida
    pela operadora (`status` em `_STATUS_GUIA_COM_AUTORIZACAO_EMITIDA`) — uma
    guia ainda `nao_enviada` nunca chegou à operadora, não há o que cancelar
    do lado dela. `TISSGuia.status` é marcado `CANCELADA` imediatamente aqui
    (reflete o cancelamento já decidido do lado da clínica); o resultado da
    chamada à operadora (sucesso ou falha após retries) é tratado
    assincronamente pela task, sem bloquear quem está cancelando o
    atendimento na recepção.

    Retorna True se o cancelamento junto à operadora foi enfileirado, False
    se não havia nada a cancelar (guia nunca enviada).
    """
    from .tasks import cancelar_guia_task

    if guia.status not in _STATUS_GUIA_COM_AUTORIZACAO_EMITIDA:
        logger.info(
            'disparar_cancelamento_guia: guia %s com status "%s" — nenhuma autorização emitida, nada a cancelar.',
            guia.id, guia.status,
        )
        TISSGuia.objects.filter(pk=guia.pk).update(status=TISSGuiaStatus.CANCELADA)
        return False

    TISSGuia.objects.filter(pk=guia.pk).update(status=TISSGuiaStatus.CANCELADA)
    cancelar_guia_task.delay(str(guia.id))
    return True
