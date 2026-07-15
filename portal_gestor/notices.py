"""
TASK-051 — resolução do aviso contextual ativo para um ClinicUser.
"""
from django.utils import timezone

from .models import ClinicUserNoticeDismissal, ProductNotice


def get_active_notice_for_user(clinic_user):
    """
    Retorna o ProductNotice mais recente que esteja `active`, dentro da
    vigência (`starts_at <= now <= ends_at`), e que este ClinicUser específico
    ainda não tenha dispensado — ou None se não houver nenhum.
    """
    if clinic_user is None:
        return None

    now = timezone.now()
    dismissed_ids = ClinicUserNoticeDismissal.objects.filter(
        clinic_user=clinic_user,
    ).values_list('notice_id', flat=True)

    return (
        ProductNotice.objects
        .filter(active=True, starts_at__lte=now, ends_at__gte=now)
        .exclude(id__in=dismissed_ids)
        .order_by('-starts_at')
        .first()
    )


def dismiss_notice(clinic_user, notice_id) -> bool:
    """Cria o registro de dispensa. Retorna False se o notice_id não existir
    (idempotente — dispensar duas vezes não é erro, get_or_create absorve)."""
    if not ProductNotice.objects.filter(id=notice_id).exists():
        return False
    ClinicUserNoticeDismissal.objects.get_or_create(clinic_user=clinic_user, notice_id=notice_id)
    return True
