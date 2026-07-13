"""
TASK-048 — agrega os dados que já existem (SystemHeartbeat, Clinic, ReportSession)
num único payload de dashboard, escopado à clínica do ClinicUser autenticado.

Recomendação da PO Healthtech (consulta em 2026-07-13): a primeira tela deve
responder rápido a "minha clínica está sincronizando?", "tenho pendência de
licença?", "preciso de relatório?" — em vez de um mural de avisos genérico.
"""
from django.conf import settings
from django.utils import timezone

from clinics.models import ClinicStatus
from metrics.models import SystemHeartbeat

from .models import ReportSession, ReportSessionStatus
from .serializers import ReportSessionSerializer

# O gateway envia heartbeat a cada ~5 min por padrão (HealthWorker, lado Go).
# "Atrasado/desconectado" = last_seen mais antigo que 2x esse intervalo —
# tolera um heartbeat perdido isolado sem já soar alarme falso.
HEARTBEAT_EXPECTED_INTERVAL_MINUTES = getattr(settings, 'HEARTBEAT_EXPECTED_INTERVAL_MINUTES', 5)
HEARTBEAT_STALE_THRESHOLD_MINUTES = HEARTBEAT_EXPECTED_INTERVAL_MINUTES * 2

# Dias antes do vencimento da licença em que o alerta começa a aparecer.
LICENSE_WARNING_DAYS = getattr(settings, 'LICENSE_WARNING_DAYS', 15)

# Quantas sessões recentes mostrar no card de atalho de relatório.
RECENT_SESSIONS_LIMIT = 5

# Status que ainda contam como "em andamento" (não finalizada nem expirada).
_ACTIVE_REPORT_STATUSES = (
    ReportSessionStatus.PENDING,
    ReportSessionStatus.KEY_DELIVERED,
    ReportSessionStatus.SYNCING,
)


def _gateway_status(clinic):
    try:
        heartbeat = SystemHeartbeat.objects.get(clinic=clinic)
    except SystemHeartbeat.DoesNotExist:
        return {
            'connected': None,  # "nunca sincronizou" — semântica diferente de "desconectado"
            'last_seen': None,
            'pending_sync': None,
            'gateway_version': None,
        }

    age = timezone.now() - heartbeat.last_seen
    connected = age.total_seconds() <= HEARTBEAT_STALE_THRESHOLD_MINUTES * 60

    return {
        'connected': connected,
        'last_seen': heartbeat.last_seen.isoformat(),
        'pending_sync': heartbeat.pending_sync,
        'gateway_version': heartbeat.gateway_version,
    }


def _license_status(clinic):
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


def _report_sessions_summary(clinic):
    recent = ReportSession.objects.filter(clinic=clinic).order_by('-created_at')[:RECENT_SESSIONS_LIMIT]
    active_count = ReportSession.objects.filter(
        clinic=clinic, status__in=_ACTIVE_REPORT_STATUSES, expires_at__gt=timezone.now(),
    ).count()

    return {
        'recent': ReportSessionSerializer(recent, many=True).data,
        'active_count': active_count,
    }


def get_dashboard_summary(clinic) -> dict:
    return {
        'gateway_status': _gateway_status(clinic),
        'license': _license_status(clinic),
        'report_sessions': _report_sessions_summary(clinic),
    }
