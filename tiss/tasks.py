"""
BACFF-014 (2026-07-30) — Cancelamento automático de guia junto à operadora,
disparado quando o atendimento/agendamento correspondente é cancelado do
lado da clínica (ver `tiss/services.py::disparar_cancelamento_guia`,
chamado por `tiss/views.py::cancelar_guia_view`).

Retry: mesmo padrão já usado em `support/tasks.py::sync_ticket_to_zoho`
(bind=True, max_retries=3, default_retry_delay=60s, `raise self.retry(exc=exc)`)
— convenção já estabelecida no projeto, não um padrão novo.

Decisão de produto do usuário (2026-07-30): cancelamento não pode falhar
silenciosamente. Se as 3 tentativas se esgotarem, registra
`TISSCancelamentoPendente` (`falhou_apos_retries=True`) — fila de trabalho
manual do suporte, visível no Django Admin (ver `tiss/admin.py`).
"""
import logging

from celery import shared_task
from django.db.models import F
from django.utils import timezone

logger = logging.getLogger(__name__)


# BACFF-014 (QA, 2026-07-29) — erro_code de `CancelamentoResultado` que
# representa uma resposta SUBSTANTIVA da operadora (ela recebeu a
# solicitação e respondeu negativamente), não uma falha de transporte.
# Reenfileirar via retry não muda esse resultado — a Orizon não vai mudar
# de ideia sobre "guia já cancelada"/"guia inexistente" só porque tentamos
# de novo 60s depois. Só `soap_network_error` (timeout/conexão) e falhas
# inesperadas são transitórias e vale a pena re-tentar.
NON_TRANSIENT_ERROR_CODES = frozenset({'guia_nao_cancelada', 'soap_fault'})


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def cancelar_guia_task(self, guia_id):
    """
    Executa `providers.resolve(operator_config).cancelar_guia(...)` para a
    guia `guia_id`. Sucesso: `TISSGuia.status` vira `CANCELADA`. Falha
    transitória (rede/operadora indisponível): re-enfileira via `self.retry`
    até 3 tentativas; na 3ª falha, registra o alerta operacional em vez de
    deixar a chamada desaparecer. Falha de negócio (a operadora respondeu e
    recusou) não é transitória — vai direto para o alerta operacional, sem
    gastar as 3 tentativas de retry (ver `NON_TRANSIENT_ERROR_CODES`).
    """
    from .models import TISSGuia, TISSGuiaStatus, TISSCancelamentoPendente
    from . import providers
    from .providers.base import ProviderError

    try:
        guia = TISSGuia.objects.select_related('clinic', 'lote', 'lote__operator_config').get(pk=guia_id)
    except TISSGuia.DoesNotExist:
        logger.warning('cancelar_guia_task: guia %s não encontrada; abortando.', guia_id)
        return

    if not guia.lote or not guia.lote.operator_config:
        logger.warning(
            'cancelar_guia_task: guia %s sem lote/operator_config associado; '
            'nada a cancelar junto à operadora.', guia_id,
        )
        return

    operator_config = guia.lote.operator_config

    try:
        resolved = providers.resolve(operator_config)
        resultado = resolved.cancelar_guia(guia.clinic, operator_config, guia)
    except ProviderError as exc:
        return _tratar_falha(self, guia, operator_config, str(exc))
    except Exception as exc:  # noqa: BLE001 — qualquer falha inesperada também deve entrar no fluxo de retry/alerta
        return _tratar_falha(self, guia, operator_config, f'erro_inesperado: {exc}')

    if not resultado.sucesso:
        erro_mensagem = resultado.erro_mensagem or resultado.erro_code
        transitorio = resultado.erro_code not in NON_TRANSIENT_ERROR_CODES
        return _tratar_falha(self, guia, operator_config, erro_mensagem, transitorio=transitorio)

    TISSGuia.objects.filter(pk=guia.pk).update(status=TISSGuiaStatus.CANCELADA)
    logger.info('cancelar_guia_task: guia %s cancelada com sucesso junto à operadora.', guia_id)


def _tratar_falha(task, guia, operator_config, erro_mensagem: str, transitorio: bool = True):
    """
    Centraliza a decisão retry-vs-alerta. `task.request.retries` é o número
    de tentativas JÁ feitas (0 na primeira execução) — quando alcança
    `max_retries`, `self.retry()` não reagenda mais uma vez: levanta a
    exceção original. Por isso checamos explicitamente ANTES de chamar
    retry, para registrar o alerta em vez de deixar a exceção subir crua.

    `transitorio=False` (erro de negócio — ver `NON_TRANSIENT_ERROR_CODES`)
    pula o retry inteiramente e registra o alerta operacional já na primeira
    tentativa: reenfileirar não muda a resposta da operadora, só atrasa o
    suporte perceber que precisa agir manualmente.
    """
    from .models import TISSCancelamentoPendente, sanitizar_erro_operadora

    if transitorio and task.request.retries < task.max_retries:
        logger.warning(
            'cancelar_guia_task: falha ao cancelar guia %s (tentativa %s/%s): %s',
            guia.id, task.request.retries + 1, task.max_retries, erro_mensagem,
        )
        raise task.retry(exc=Exception(erro_mensagem))

    if transitorio:
        logger.error(
            'cancelar_guia_task: guia %s falhou após %s tentativas — registrando alerta operacional.',
            guia.id, task.max_retries,
        )
    else:
        logger.error(
            'cancelar_guia_task: guia %s — a operadora recusou o cancelamento (erro de negócio, não '
            'transitório); registrando alerta operacional sem gastar tentativas de retry: %s',
            guia.id, erro_mensagem,
        )

    pendente, _created = TISSCancelamentoPendente.objects.get_or_create(
        guia=guia,
        defaults={'clinic': guia.clinic},
    )
    pendente.tentativas = task.request.retries + 1
    pendente.falhou_apos_retries = True
    # Segurança/LGPD (BACFF-014, revisão 2026-07-30): `erro_mensagem` pode
    # conter `descricaoErro` bruto devolvido pela Orizon (texto livre de
    # sistema externo) — sanitiza antes de persistir/expor no Django Admin.
    # Ver `models.sanitizar_erro_operadora`.
    pendente.ultimo_erro = sanitizar_erro_operadora(erro_mensagem)
    pendente.resolvido = False
    pendente.save(update_fields=['tentativas', 'falhou_apos_retries', 'ultimo_erro', 'resolvido', 'updated_at'])


# ---------------------------------------------------------------------------
# BO-08.5 — Polling periódico de autorizações "Em Análise"
# ---------------------------------------------------------------------------
#
# Decisão de infra (2026-08-06, mesma sessão que introduziu CELERY_BEAT_
# SCHEDULE pela primeira vez no projeto para EDGW-038/WhatsApp): task
# PERIÓDICA via Celery Beat, não management command + cron externo — padrão
# antigo (`purgar_operator_call_log`, `purge_old_backups`) só existia porque
# não havia Beat configurado. Ver `CELERY_BEAT_SCHEDULE` em
# `syncro_backoffice/settings.py`.
#
# Diferente de `cancelar_guia_task` (disparada uma vez por evento, com
# retry/backoff próprio): esta task roda em CICLOS regulares e varre TODAS as
# pendências não resolvidas a cada execução — o "retry" de uma pendência
# individual não é `self.retry()`, é simplesmente ela continuar aparecendo no
# próximo ciclo porque `resolvido` continua False. Por isso não usa
# `bind=True`/`max_retries` como `cancelar_guia_task`: não há um número fixo
# de tentativas, a pendência fica na fila até a operadora responder ou até
# alguém investigar manualmente via Django Admin.


@shared_task
def consultar_autorizacoes_pendentes_task():
    """
    Varre `TISSAutorizacaoPendente` com `resolvido=False`, consulta o status
    de cada uma via `provider.consultar_status_autorizacao`
    (`tissSolicitacaoStatusAutorizacao_Operation` na Orizon) e atualiza o
    registro local quando a operadora já respondeu em definitivo (autorizado/
    negado).

    Isolamento de falha por item: uma pendência com erro de rede/parsing
    NUNCA impede as demais de serem consultadas no mesmo ciclo — cada
    pendência é tratada em `_consultar_uma_pendente_status`, que captura
    qualquer exceção própria. A task inteira também nunca propaga exceção
    para o Celery: erro de infraestrutura (ex.: banco indisponível ao montar
    o queryset) é logado e a task termina sem lançar, para não gerar retry
    em avalanche do Beat nem marcar a execução como falha ruidosa — o próximo
    ciclo agendado (5-10min) tenta de novo naturalmente.
    """
    from .models import TISSAutorizacaoPendente

    try:
        pendentes = list(
            TISSAutorizacaoPendente.objects.select_related('clinic', 'operator_config')
            .filter(resolvido=False)
        )
    except Exception:  # noqa: BLE001 — falha de infra ao montar a query não pode propagar para o Celery
        logger.exception('consultar_autorizacoes_pendentes_task: falha ao buscar pendências; tenta no próximo ciclo.')
        return

    if not pendentes:
        logger.info('consultar_autorizacoes_pendentes_task: nenhuma autorização pendente para consultar.')
        return

    logger.info(
        'consultar_autorizacoes_pendentes_task: consultando %s autorização(ões) pendente(s).', len(pendentes),
    )
    for pendente in pendentes:
        _consultar_uma_pendente_status(pendente)


def _consultar_uma_pendente_status(pendente):
    """
    Consulta o status de UMA `TISSAutorizacaoPendente` e persiste o
    resultado. Nunca levanta — qualquer falha (rede, timeout, provider
    desativado/não registrado, erro inesperado de parsing) é logada e a
    pendência permanece `resolvido=False` para o próximo ciclo tentar de
    novo; só registra `tentativas_consulta`/`ultimo_erro_consulta` para dar
    visibilidade no Django Admin de quantas vezes já tentamos sem sucesso.

    Idempotência (requisito BO-08.5): a transição para terminal usa
    `.filter(pk=pendente.pk, resolvido=False).update(...)` — mesmo se este
    método fosse chamado duas vezes para a MESMA pendência (não deveria
    acontecer dentro de um único ciclo, já que o queryset é materializado uma
    vez por execução, mas é uma garantia barata contra corrida entre workers
    concorrentes do Beat), a segunda chamada não encontra linha para
    atualizar (já `resolvido=True`) e não duplica efeito nenhum (sem
    log duplicado, sem alerta duplicado — só a condição do WHERE muda).
    """
    from .models import TISSAutorizacaoPendente, TISSAutorizacaoSituacao, sanitizar_erro_operadora
    from . import providers
    from .providers.base import ProviderError

    try:
        provider = providers.resolve(pendente.operator_config)
    except ProviderError as exc:
        logger.warning(
            'consultar_autorizacoes_pendentes_task: operator_config %s indisponível (%s); '
            'pendência %s permanece para o próximo ciclo.',
            pendente.operator_config_id, exc.code, pendente.id,
        )
        TISSAutorizacaoPendente.objects.filter(pk=pendente.pk, resolvido=False).update(
            tentativas_consulta=F('tentativas_consulta') + 1,
            ultima_consulta_em=timezone.now(),
            ultimo_erro_consulta=sanitizar_erro_operadora(f'{exc.code}: {exc}'),
        )
        return

    try:
        resultado = provider.consultar_status_autorizacao(
            pendente.clinic, pendente.operator_config,
            numero_guia_prestador=pendente.numero_guia_prestador,
            numero_guia_operadora=pendente.numero_guia_operadora,
        )
    except providers.OperacaoNaoSuportada:
        # Provider trocado para um que não implementa a operação depois da
        # pendência já existir (ex.: mudança de gateway_provider no admin).
        # Não é erro transitório — registra e segue para a próxima pendência,
        # sem incrementar tentativas indefinidamente por algo que nenhum
        # ciclo futuro vai resolver sozinho (precisa de ação humana).
        logger.warning(
            'consultar_autorizacoes_pendentes_task: provider de %s não implementa consulta de status; '
            'pendência %s exige revisão manual.',
            pendente.operator_config_id, pendente.id,
        )
        return
    except Exception as exc:  # noqa: BLE001 — timeout/erro de rede/parsing nunca pode derrubar o ciclo
        logger.error(
            'consultar_autorizacoes_pendentes_task: erro inesperado consultando pendência %s: %s',
            pendente.id, type(exc).__name__,
        )
        TISSAutorizacaoPendente.objects.filter(pk=pendente.pk, resolvido=False).update(
            tentativas_consulta=F('tentativas_consulta') + 1,
            ultima_consulta_em=timezone.now(),
            ultimo_erro_consulta=sanitizar_erro_operadora(f'erro_inesperado: {exc}'),
        )
        return

    if not resultado.sucesso:
        # Falha de CONSULTA (rede/fault da operadora na própria consulta de
        # status) — trivialmente re-tentável no próximo ciclo, não altera
        # `situacao` da pendência.
        logger.warning(
            'consultar_autorizacoes_pendentes_task: falha ao consultar status da pendência %s (%s): %s',
            pendente.id, resultado.erro_code, resultado.erro_mensagem,
        )
        TISSAutorizacaoPendente.objects.filter(pk=pendente.pk, resolvido=False).update(
            tentativas_consulta=F('tentativas_consulta') + 1,
            ultima_consulta_em=timezone.now(),
            ultimo_erro_consulta=sanitizar_erro_operadora(resultado.erro_mensagem or resultado.erro_code),
        )
        return

    if resultado.situacao == TISSAutorizacaoSituacao.EM_ANALISE:
        # Operadora respondeu, mas a decisão ainda não saiu — só registra que
        # tentamos, sem mudar `situacao`/`resolvido`.
        TISSAutorizacaoPendente.objects.filter(pk=pendente.pk, resolvido=False).update(
            tentativas_consulta=F('tentativas_consulta') + 1,
            ultima_consulta_em=timezone.now(),
            ultimo_erro_consulta='',
        )
        return

    # AUTORIZADO ou NEGADO — resposta TERMINAL da operadora. `resolvido=False`
    # no filtro garante que, se por algum motivo esta pendência já tivesse
    # sido resolvida por outra execução concorrente, este UPDATE simplesmente
    # não afeta nenhuma linha (idempotência — sem log/alerta duplicado).
    linhas_afetadas = TISSAutorizacaoPendente.objects.filter(pk=pendente.pk, resolvido=False).update(
        situacao=resultado.situacao,
        numero_guia_operadora=resultado.numero_guia_operadora or pendente.numero_guia_operadora,
        codigo_glosa=resultado.codigo_glosa,
        descricao_glosa=sanitizar_erro_operadora(resultado.descricao_glosa),
        resolvido=True,
        tentativas_consulta=F('tentativas_consulta') + 1,
        ultima_consulta_em=timezone.now(),
        ultimo_erro_consulta='',
    )
    if linhas_afetadas:
        logger.info(
            'consultar_autorizacoes_pendentes_task: pendência %s resolvida (%s).',
            pendente.id, resultado.situacao,
        )
