from rest_framework.permissions import BasePermission, IsAuthenticated
from clinics.models import Clinic, ClinicStatus


class IsAuthenticatedByLicenseKeyOrJWT(BasePermission):
    """
    Aceita autenticação por:
    1. License key (X-License-Key header) — para Edge Gateway abrir tickets
    2. JWT token — para Admin gerenciar tickets
    """

    def has_permission(self, request, view):
        # Tenta license_key primeiro (Edge Gateway)
        license_key = request.headers.get('X-License-Key', '').strip()
        if license_key:
            try:
                clinic = Clinic.objects.get(license_key=license_key, status=ClinicStatus.ACTIVE)
                request.clinic = clinic
                request.user = None  # Edge não tem user
                return True
            except (Clinic.DoesNotExist, Exception):
                return False

        # Tenta JWT (Admin)
        if request.user and request.user.is_authenticated:
            return True

        return False
