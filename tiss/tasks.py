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

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def cancelar_guia_task(self, guia_id):
    """
    Executa `providers.resolve(operator_config).cancelar_guia(...)` para a
    guia `guia_id`. Sucesso: `TISSGuia.status` vira `CANCELADA`. Falha
    (rede/operadora indisponível): re-enfileira via `self.retry` até 3
    tentativas; na 3ª falha, registra o alerta operacional em vez de deixar
    a chamada desaparecer.
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
        return _tratar_falha(self, guia, operator_config, resultado.erro_mensagem or resultado.erro_code)

    TISSGuia.objects.filter(pk=guia.pk).update(status=TISSGuiaStatus.CANCELADA)
    logger.info('cancelar_guia_task: guia %s cancelada com sucesso junto à operadora.', guia_id)


def _tratar_falha(task, guia, operator_config, erro_mensagem: str):
    """
    Centraliza a decisão retry-vs-alerta. `task.request.retries` é o número
    de tentativas JÁ feitas (0 na primeira execução) — quando alcança
    `max_retries`, `self.retry()` não reagenda mais uma vez: levanta a
    exceção original. Por isso checamos explicitamente ANTES de chamar
    retry, para registrar o alerta em vez de deixar a exceção subir crua.
    """
    from .models import TISSCancelamentoPendente

    if task.request.retries < task.max_retries:
        logger.warning(
            'cancelar_guia_task: falha ao cancelar guia %s (tentativa %s/%s): %s',
            guia.id, task.request.retries + 1, task.max_retries, erro_mensagem,
        )
        raise task.retry(exc=Exception(erro_mensagem))

    logger.error(
        'cancelar_guia_task: guia %s falhou após %s tentativas — registrando alerta operacional.',
        guia.id, task.max_retries,
    )
    pendente, _created = TISSCancelamentoPendente.objects.get_or_create(
        guia=guia,
        defaults={'clinic': guia.clinic},
    )
    pendente.tentativas = task.max_retries + 1
    pendente.falhou_apos_retries = True
    pendente.ultimo_erro = erro_mensagem
    pendente.resolvido = False
    pendente.save(update_fields=['tentativas', 'falhou_apos_retries', 'ultimo_erro', 'resolvido', 'updated_at'])
