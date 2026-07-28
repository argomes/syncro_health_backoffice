"""
Dashboard de Serviços — home do /admin/.

Ligado via UNFOLD["DASHBOARD_CALLBACK"] (settings.py). Implementa apenas os
cards que o ADMIN-DASHBOARD-REDESIGN.md (.claude/tasks/) classificou como
"usa dado já existente, sem decisão pendente do Tech Lead":

- Clientes: ativos / inativos (suspensos+cancelados) — NÃO inclui
  "inadimplentes": isso depende de um estado ClinicStatus.OVERDUE que ainda
  não existe (ver §5.1 do documento), e é decisão de negócio pendente.
- Gateways: online (heartbeat < 30 min) vs total, reaproveitando o mesmo
  corte já usado em metrics.admin.SystemHeartbeatAdmin.last_seen_status.
- Erros (24h): contagem de metrics.SystemLog level=error nas últimas 24h.

Regra inegociável do documento (§4.5): o callback NUNCA pode rodar query
pesada/chamada de rede direto no request — os números vêm de
cache.get_or_set(..., 60) sobre o Redis já configurado (CACHE_URL).

Isolamento por tenant (risco nº1 apontado pela revisão de segurança do
documento): o callback roda fora de qualquer ModelAdmin, portanto fora do
TenantScopedAdminMixin. Um analista com ClinicAccess para 2 de N clínicas
deve ver contagens das 2, nunca do total — a lógica de escopo abaixo
replica exatamente a do mixin (accounts.ClinicAccess, revoked_at__isnull).
"""
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from clinics.models import Clinic, ClinicStatus
from metrics.models import SystemHeartbeat, SystemLog, LogLevel

GATEWAY_ONLINE_CUTOFF_MINUTES = 30
ERROR_LOG_WINDOW_HOURS = 24
DASHBOARD_CACHE_SECONDS = 60


def _allowed_clinic_ids(user):
    """
    Mesma regra de accounts.ClinicAccess usada em
    syncro_backoffice.base_admin.TenantScopedAdminMixin.get_queryset —
    superuser e SupportUser role=admin enxergam tudo; os demais só as
    clínicas com ClinicAccess ativo (revoked_at is null).

    Retorna None para "sem restrição" (ver tudo) e um queryset/list de ids
    para "restrito a estas clínicas".
    """
    if user.is_superuser:
        return None
    if getattr(user, 'role', None) == 'admin':
        return None

    from accounts.models import ClinicAccess

    return list(
        ClinicAccess.objects.filter(
            support_user=user,
            revoked_at__isnull=True,
        ).values_list('clinic_id', flat=True)
    )


def _clinics_card(allowed_clinic_ids):
    qs = Clinic.objects.all()
    if allowed_clinic_ids is not None:
        qs = qs.filter(pk__in=allowed_clinic_ids)

    return {
        'active': qs.filter(status=ClinicStatus.ACTIVE).count(),
        'inactive': qs.filter(
            status__in=[ClinicStatus.SUSPENDED, ClinicStatus.CANCELLED]
        ).count(),
    }


def _gateways_card(allowed_clinic_ids):
    qs = SystemHeartbeat.objects.all()
    if allowed_clinic_ids is not None:
        qs = qs.filter(clinic_id__in=allowed_clinic_ids)

    cutoff = timezone.now() - timedelta(minutes=GATEWAY_ONLINE_CUTOFF_MINUTES)
    total = qs.count()
    online = qs.filter(last_seen__gte=cutoff).count()
    return {'online': online, 'total': total}


def _errors_card(allowed_clinic_ids):
    qs = SystemLog.objects.filter(level=LogLevel.ERROR)
    if allowed_clinic_ids is not None:
        qs = qs.filter(clinic_id__in=allowed_clinic_ids)

    since = timezone.now() - timedelta(hours=ERROR_LOG_WINDOW_HOURS)
    return {'count': qs.filter(occurred_at__gte=since).count()}


def dashboard_callback(request, context):
    user = request.user

    # Cache por usuário: o escopo (allowed_clinic_ids) varia por analista,
    # então a chave precisa ser por-usuário, não global — senão um analista
    # restrito herdaria o número cacheado de um superuser (ou vice-versa).
    cache_key = f'admin_dashboard_cards:{user.pk}'
    cards = cache.get(cache_key)
    if cards is None:
        allowed_clinic_ids = _allowed_clinic_ids(user)
        cards = {
            'clinics': _clinics_card(allowed_clinic_ids),
            'gateways': _gateways_card(allowed_clinic_ids),
            'errors': _errors_card(allowed_clinic_ids),
        }
        cache.set(cache_key, cards, DASHBOARD_CACHE_SECONDS)

    context['dashboard_cards'] = cards
    return context
