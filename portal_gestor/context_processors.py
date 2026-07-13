"""
TASK-051 — injeta o aviso contextual ativo (se houver) no contexto de todo
template renderizado sob /portal/*, sem precisar que cada view popule isso
manualmente. `request.clinic_user` já é injetado pelo ClinicPortalAuthMiddleware
(TASK-047) antes de qualquer view rodar — aqui só lemos, nunca escrevemos.
"""
from .notices import get_active_notice_for_user


def active_notice(request):
    clinic_user = getattr(request, 'clinic_user', None)
    return {'active_notice': get_active_notice_for_user(clinic_user)}
