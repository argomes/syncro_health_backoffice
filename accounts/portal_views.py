from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from clinics.permissions import IsClinicUser
from syncro_backoffice.throttling import LoginRateThrottle

from .authentication import ClinicJWTAuthentication
from .portal_serializers import ClinicTokenObtainPairSerializer, ClinicTokenRefreshSerializer


class ClinicTokenObtainPairView(APIView):
    """
    POST /portal/api/auth/login/ — login de ClinicUser (admin/gerente da clínica).

    Não usa TokenObtainPairView padrão porque ClinicUser não é o AUTH_USER_MODEL
    (accounts.SupportUser) — ver accounts/portal_serializers.py.
    """

    permission_classes = []
    authentication_classes = []
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        serializer = ClinicTokenObtainPairSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data, status=status.HTTP_200_OK)


class ClinicTokenRefreshView(APIView):
    """
    POST /portal/api/auth/refresh/ — renova o access token de um ClinicUser.

    Não usa TokenRefreshView padrão (ver ClinicTokenRefreshSerializer para o motivo).
    """

    permission_classes = []
    authentication_classes = []
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        serializer = ClinicTokenRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data, status=status.HTTP_200_OK)


class ClinicUserMeView(APIView):
    """
    GET /portal/api/auth/me/ — smoke-test da autenticação: confirma que o
    ClinicJWTAuthentication resolve o token para o ClinicUser certo, escopado
    à própria clínica (nunca a outra).
    """

    authentication_classes = [ClinicJWTAuthentication]
    permission_classes = [IsClinicUser]

    def get(self, request):
        user = request.user
        return Response({
            'id': str(user.id),
            'email': user.email,
            'name': user.name,
            'clinic_id': str(user.clinic_id),
            'clinic_name': user.clinic.name,
        })
