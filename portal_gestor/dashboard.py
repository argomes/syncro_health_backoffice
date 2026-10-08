"""
TASK-048 — agrega os dados que já existem (SystemHeartbeat, Clinic, ReportSession)
num único payload de dashboard, escopado à clínica do ClinicUser autenticado.

Recomendação da PO Healthtech (consulta em 2026-07-13): a primeira tela deve
responder rápido a "minha clínica está sincronizando?", "tenho pendência de
licença?", "preciso de relatório?" — em vez de um mural de avisos genérico.

O1 (PO, 2026-10-08) — o card de sincronização precisa ser HONESTO: antes ele
ficava verde só porque o gateway mandou heartbeat recente, mesmo com o
gateway sem conexão com o Postgres da nuvem (`sync_connected=False`) ou com
uma fila de envio acumulada (`pending_sync`). Na demo, a objeção "o sistema
só funciona local" é respondida mostrando que o portal sabe exatamente
quando a nuvem está (ou não) recebendo os dados — um verde mentiroso
destrói essa confiança no primeiro teste do prospect. A regra vive em
`compute_sync_status` (função pura, testável sem banco), nunca no template.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Optional

from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone

from clinics.models import ClinicStatus
from metrics.models import SystemHeartbeat

from .models import ReportSession, ReportSessionStatus
from .serializers import ReportSessionSerializer

# O gateway envia heartbeat a cada ~5 min por padrão (HealthWorker, lado Go).
# "Atrasado/desconectado" = last_seen mais antigo que 2x esse intervalo —
# tolera um heartbeat perdido isolado sem já soar alarme falso. Com o
# default de 5 min, heartbeat com mais de 10 min = vermelho no card.
HEARTBEAT_EXPECTED_INTERVAL_MINUTES: Final[int] = getattr(
    settings, 'HEARTBEAT_EXPECTED_INTERVAL_MINUTES', 5,
)
HEARTBEAT_STALE_THRESHOLD_MINUTES: Final[int] = HEARTBEAT_EXPECTED_INTERVAL_MINUTES * 2

# Quantos itens no sync_outbox do gateway ainda contam como "Sincronizado".
# O SyncWorker do gateway drena a fila em ciclos, então uma clínica em uso
# normal SEMPRE tem alguns registros em trânsito entre um ciclo e outro
# (recepcionista acabou de salvar um agendamento). Exigir zero faria o card
# piscar amarelo a cada clique na recepção — ruído que ensina o gestor a
# ignorar o alerta. Acima disso, a fila está acumulando de verdade.
SYNC_PENDING_TOLERANCE: Final[int] = getattr(settings, 'SYNC_PENDING_TOLERANCE', 5)

# Dias antes do vencimento da licença em que o alerta começa a aparecer.
LICENSE_WARNING_DAYS: Final[int] = getattr(settings, 'LICENSE_WARNING_DAYS', 15)

# Quantas sessões recentes mostrar no card de atalho de relatório.
RECENT_SESSIONS_LIMIT: Final[int] = 5

# Status que ainda contam como "em andamento" (não finalizada nem expirada).
_ACTIVE_REPORT_STATUSES: Final[tuple[str, ...]] = (
    ReportSessionStatus.PENDING,
    ReportSessionStatus.KEY_DELIVERED,
    ReportSessionStatus.SYNCING,
)

# Estados do card de sincronização. Os valores são estáveis (fazem parte do
# payload JSON da TASK-048 e viram classe CSS `sync-<estado>`) — renomear
# quebra consumidores; os rótulos pt-BR é que podem mudar livremente.
SYNC_STATE_OK: Final[str] = 'ok'  # verde
SYNC_STATE_SYNCING: Final[str] = 'syncing'  # amarelo
SYNC_STATE_OFFLINE: Final[str] = 'offline'  # vermelho
SYNC_STATE_NEVER: Final[str] = 'never'  # cinza

_SYNC_BADGE_BY_STATE: Final[dict[str, str]] = {
    SYNC_STATE_OK: 'green',
    SYNC_STATE_SYNCING: 'yellow',
    SYNC_STATE_OFFLINE: 'red',
    SYNC_STATE_NEVER: 'gray',
}


@dataclass(frozen=True)
class SyncStatus:
    """Estado já decidido do card de sincronização, pronto para exibição.

    Attributes:
        state: Um dos ``SYNC_STATE_*`` (contrato estável, usado no CSS/JSON).
        label: Rótulo pt-BR curto do badge.
        badge: Cor do badge (``green``/``yellow``/``red``/``gray``).
        pending_sync: Itens na fila de envio do gateway; ``None`` se nunca
            houve heartbeat.
        last_seen: Instante (aware) do último heartbeat; ``None`` se nunca
            houve.
        last_seen_ago: Texto relativo pt-BR ("há 3 min"); vazio se nunca.
    """

    state: str
    label: str
    badge: str
    pending_sync: Optional[int]
    last_seen: Optional[datetime]
    last_seen_ago: str


def humanize_age_ptbr(age: timedelta) -> str:
    """Formata a idade do último heartbeat como texto relativo em pt-BR.

    O gestor quer saber "quanto tempo faz", não o timestamp exato — o
    absoluto continua disponível no template (dd/mm/aaaa HH:MM) para quem
    precisar conferir. Idade negativa (relógio do servidor ligeiramente
    adiantado em relação ao momento de gravação) é tratada como "agora".

    Args:
        age: Diferença entre agora e o último heartbeat.

    Returns:
        Texto como "agora mesmo", "há 1 min", "há 2 h" ou "há 3 dias".
    """
    total_minutes = int(age.total_seconds() // 60)
    if total_minutes < 1:
        return 'agora mesmo'
    if total_minutes < 60:
        return f'há {total_minutes} min'
    hours = total_minutes // 60
    if hours < 24:
        return f'há {hours} h'
    days = hours // 24
    return 'há 1 dia' if days == 1 else f'há {days} dias'


def compute_sync_status(heartbeat: Optional[SystemHeartbeat], now: datetime) -> SyncStatus:
    """Decide o estado do card de sincronização a partir do último heartbeat.

    Regra (O1, PO 2026-10-08), avaliada nesta ordem:

    1. Sem heartbeat algum → cinza "Nunca sincronizou" (o gateway nunca foi
       instalado/ativado — semântica diferente de "caiu").
    2. Heartbeat mais velho que ``HEARTBEAT_STALE_THRESHOLD_MINUTES`` OU
       ``sync_connected=False`` → vermelho "Sem conexão com a nuvem". Os dois
       casos são o mesmo problema para o gestor: os dados da clínica não estão
       chegando na nuvem. Heartbeat velho tem precedência sobre a fila porque
       ``pending_sync``/``sync_connected`` de um heartbeat velho já não
       descrevem o presente.
    3. Conectado com mais de ``SYNC_PENDING_TOLERANCE`` itens na fila →
       amarelo "Sincronizando — N pendentes".
    4. Caso contrário → verde "Sincronizado".

    Args:
        heartbeat: Último heartbeat da clínica, ou ``None`` se nunca houve.
        now: Instante de referência (injetado para testes determinísticos).

    Returns:
        ``SyncStatus`` imutável com estado, rótulo pt-BR e metadados.
    """
    if heartbeat is None:
        return SyncStatus(
            state=SYNC_STATE_NEVER,
            label='Nunca sincronizou',
            badge=_SYNC_BADGE_BY_STATE[SYNC_STATE_NEVER],
            pending_sync=None,
            last_seen=None,
            last_seen_ago='',
        )

    age = now - heartbeat.last_seen
    pending = max(int(heartbeat.pending_sync or 0), 0)
    heartbeat_stale = age > timedelta(minutes=HEARTBEAT_STALE_THRESHOLD_MINUTES)

    if heartbeat_stale or not heartbeat.sync_connected:
        state, label = SYNC_STATE_OFFLINE, 'Sem conexão com a nuvem'
    elif pending > SYNC_PENDING_TOLERANCE:
        state, label = SYNC_STATE_SYNCING, f'Sincronizando — {pending} pendentes'
    else:
        state, label = SYNC_STATE_OK, 'Sincronizado'

    return SyncStatus(
        state=state,
        label=label,
        badge=_SYNC_BADGE_BY_STATE[state],
        pending_sync=pending,
        last_seen=heartbeat.last_seen,
        last_seen_ago=humanize_age_ptbr(age),
    )


def get_last_heartbeat(clinic: Any) -> Optional[SystemHeartbeat]:
    """Retorna o heartbeat da clínica (OneToOne) ou ``None`` se nunca houve."""
    return SystemHeartbeat.objects.filter(clinic=clinic).first()


def get_sync_status(clinic: Any) -> SyncStatus:
    """Atalho: estado de sincronização da clínica no instante atual."""
    return compute_sync_status(get_last_heartbeat(clinic), timezone.now())


def _gateway_status(clinic: Any) -> dict[str, Any]:
    """Bloco ``gateway_status`` do payload JSON (TASK-048).

    ``connected`` mantém a semântica original (heartbeat recente, ``None`` =
    nunca sincronizou) para não quebrar consumidores da API. Os campos
    ``sync_connected`` e ``sync_state`` são aditivos (O1) e carregam a
    leitura honesta usada pelo card do portal.
    """
    heartbeat = get_last_heartbeat(clinic)
    if heartbeat is None:
        return {
            'connected': None,  # "nunca sincronizou" — semântica diferente de "desconectado"
            'sync_connected': None,
            'sync_state': SYNC_STATE_NEVER,
            'last_seen': None,
            'pending_sync': None,
            'gateway_version': None,
        }

    now = timezone.now()
    age = now - heartbeat.last_seen
    connected = age.total_seconds() <= HEARTBEAT_STALE_THRESHOLD_MINUTES * 60

    return {
        'connected': connected,
        'sync_connected': heartbeat.sync_connected,
        'sync_state': compute_sync_status(heartbeat, now).state,
        'last_seen': heartbeat.last_seen.isoformat(),
        'pending_sync': heartbeat.pending_sync,
        'gateway_version': heartbeat.gateway_version,
    }


def get_license_status(clinic: Any) -> dict[str, Any]:
    """Bloco ``license`` do payload: status, vencimento e se exige alerta."""
    expires_at = clinic.license_expires_at
    days_until_expiry = None
    warning = clinic.status != ClinicStatus.ACTIVE

    if expires_at is not None:
        days_until_expiry = (expires_at - timezone.now()).days
        if days_until_expiry <= LICENSE_WARNING_DAYS:
            warning = True

    return {
        'status': clinic.status,
        'expires_at': expires_at.isoformat() if expires_at else None,
        'days_until_expiry': days_until_expiry,
        'warning': warning,
    }


# Alias mantido para os testes/consumidores existentes da TASK-048.
_license_status = get_license_status


def get_recent_report_sessions(clinic: Any) -> QuerySet[ReportSession]:
    """Últimas sessões de relatório da clínica (sempre escopado ao tenant).

    Devolve instâncias de modelo (não dicts serializados) para que o
    template HTML use ``get_status_display`` e filtros de data sobre
    ``datetime`` reais — o serializer JSON transforma datas em string, e
    ``|date`` sobre string renderiza vazio (bug O2).
    """
    return ReportSession.objects.filter(clinic=clinic).order_by('-created_at')[:RECENT_SESSIONS_LIMIT]


def _report_sessions_summary(clinic: Any) -> dict[str, Any]:
    recent = get_recent_report_sessions(clinic)
    active_count = ReportSession.objects.filter(
        clinic=clinic, status__in=_ACTIVE_REPORT_STATUSES, expires_at__gt=timezone.now(),
    ).count()

    return {
        'recent': ReportSessionSerializer(recent, many=True).data,
        'active_count': active_count,
    }


def get_dashboard_summary(clinic: Any) -> dict[str, Any]:
    """Payload JSON consolidado do dashboard (contrato da TASK-048)."""
    return {
        'gateway_status': _gateway_status(clinic),
        'license': get_license_status(clinic),
        'report_sessions': _report_sessions_summary(clinic),
    }
