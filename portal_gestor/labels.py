"""
O2 (PO, 2026-10-08) — rótulos pt-BR únicos para valores crus que chegam ao
portal sem ``choices`` Django próprios.

Status de modelos do próprio backoffice (``ReportSession``, ``Clinic``,
``Ticket``) já têm ``TextChoices`` em pt-BR e devem ser exibidos via
``get_<campo>_display`` — NÃO duplicar esses rótulos aqui. Este módulo cobre
só o que vem do Postgres da clínica (escrito pelo Edge Gateway em Go), onde
não existe modelo Django para carregar ``choices``.
"""
from __future__ import annotations

from typing import Final, Optional

# Espelho de syncro_gateway/internal/core/domain/appointment.go (Status*).
# Se o gateway ganhar um status novo, ele aparece cru até ser adicionado
# aqui — preferível a esconder o valor ou quebrar a página.
APPOINTMENT_STATUS_LABELS: Final[dict[str, str]] = {
    'scheduled': 'Agendado',
    'confirmed': 'Confirmado',
    'waiting': 'Aguardando atendimento',
    'in_progress': 'Em atendimento',
    'completed': 'Concluído',
    'cancelled': 'Cancelado',
    'no_show': 'Não compareceu',
    'rescheduled': 'Reagendado',
}


def appointment_status_label(raw_status: Optional[str]) -> str:
    """Traduz o status cru de um agendamento para rótulo pt-BR.

    Args:
        raw_status: Valor gravado pelo gateway (ex.: ``"no_show"``).

    Returns:
        Rótulo pt-BR; ``"—"`` para vazio; o próprio valor se desconhecido
        (não esconde dado novo do gestor só porque o mapa está desatualizado).
    """
    if not raw_status:
        return '—'
    return APPOINTMENT_STATUS_LABELS.get(raw_status, raw_status)
