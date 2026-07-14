from rest_framework.permissions import BasePermission

from accounts.models import SupportUser


class IsTISSAuthorized(BasePermission):
    """
    RBAC do módulo TISS: só SupportUser autenticado (admin ou billing —
    faturamento é quem opera TISS no dia a dia; support genérico não mexe em
    lote/envio, só visualiza). Isolamento por clínica é feito no
    get_queryset de cada view (via ClinicAccess), não aqui — esta permission
    só decide QUEM pode entrar no módulo, não QUAIS clínicas.
    """
    allowed_roles = {SupportUser.Role.ADMIN, SupportUser.Role.BILLING}

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        role = getattr(user, 'role', None)
        return role in self.allowed_roles
