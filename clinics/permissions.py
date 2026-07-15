from rest_framework.permissions import BasePermission
from .models import Clinic, ClinicStatus


class IsAuthenticatedByLicenseKey(BasePermission):
    """
    Autentica o app desktop via license_key no header X-License-Key.
    Injeta request.clinic para uso nas views.
    Só permite clínicas com status ACTIVE.
    """

    def has_permission(self, request, view):
        license_key = request.headers.get('X-License-Key', '').strip()
        if not license_key:
            return False
        try:
            clinic = Clinic.objects.get(license_key=license_key, status=ClinicStatus.ACTIVE)
            request.clinic = clinic
            return True
        except (Clinic.DoesNotExist, Exception):
            return False


class IsAuthenticatedByAnyLicenseKey(BasePermission):
    """
    Autentica o app desktop via license_key no header X-License-Key.
    Injeta request.clinic para uso nas views.
    Aceita clínicas em qualquer status — a view decide o comportamento por status.
    """

    def has_permission(self, request, view):
        license_key = request.headers.get('X-License-Key', '').strip()
        if not license_key:
            return False
        try:
            clinic = Clinic.objects.get(license_key=license_key)
            request.clinic = clinic
            return True
        except (Clinic.DoesNotExist, Exception):
            return False


class IsServiceTokenAuthenticated(BasePermission):
    """
    Permite acesso apenas se a requisição foi autenticada via Service Token.
    Verifica se request.auth (o payload do JWT) está presente e contém clinic_id.
    """

    def has_permission(self, request, view):
        return bool(
            request.auth and
            isinstance(request.auth, dict) and
            'clinic_id' in request.auth
        )


class IsClinicUser(BasePermission):
    """
    Permite acesso apenas a requisições autenticadas via ClinicJWTAuthentication
    (accounts.authentication.ClinicJWTAuthentication), isto é, request.user é um
    accounts.models.ClinicUser ativo.

    Usar em conjunto com authentication_classes = [ClinicJWTAuthentication] nas
    views do portal_gestor — nunca combinar com o DEFAULT_AUTHENTICATION_CLASSES
    global (que resolve para SupportUser).
    """

    def has_permission(self, request, view):
        from accounts.models import ClinicUser
        return isinstance(request.user, ClinicUser) and request.user.is_active

