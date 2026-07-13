from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import ClinicAccess, SupportUser
from clinics.models import Clinic
from clinics.permissions import IsAuthenticatedByLicenseKey
from metrics.serializers import SystemLogSerializer
from portal_gestor.services import get_pending_session_key_payload
from .models import SystemHeartbeat, SystemLog, LogLevel


@api_view(['POST'])
@permission_classes([IsAuthenticatedByLicenseKey])
def heartbeat(request):
    clinic = request.clinic
    data = request.data

    SystemHeartbeat.objects.update_or_create(
        clinic=clinic,
        defaults={
            'gateway_version': data.get('gateway_version', ''),
            'os_info': data.get('os_info', ''),
            'db_size_mb': data.get('db_size_mb', 0),
            'pending_sync': data.get('pending_sync', 0),
            'sync_connected': data.get('sync_connected', False),
        },
    )

    response_body = {'ok': True}

    # TASK-042/041: se houver uma ReportSession pendente para esta clínica,
    # entrega a TemporaryKey cifrada + a janela de resync no mesmo heartbeat —
    # sem endpoint dedicado no gateway (ver syncro_gateway health_worker.go).
    session_payload = get_pending_session_key_payload(clinic)
    if session_payload is not None:
        response_body.update(session_payload)

    return Response(response_body, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticatedByLicenseKey])
def logs(request):
    clinic = request.clinic
    entries = request.data.get('logs', [])

    if not isinstance(entries, list):
        return Response({'error': 'logs must be a list'}, status=status.HTTP_400_BAD_REQUEST)

    valid_levels = {c[0] for c in LogLevel.choices}
    objects = []
    for entry in entries:
        level = entry.get('level', 'info')
        if level not in valid_levels:
            level = LogLevel.INFO
        occurred_at = parse_datetime(entry.get('occurred_at', ''))
        if occurred_at is None:
            continue
        objects.append(SystemLog(
            clinic=clinic,
            level=level,
            message=entry.get('message', ''),
            context=entry.get('context') or {},
            occurred_at=occurred_at,
        ))

    SystemLog.objects.bulk_create(objects)
    return Response({'created': len(objects)}, status=status.HTTP_201_CREATED)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard_metrics(request, clinic_id):
    """
    GET /api/metrics/dashboard/{clinic_id}
    Retorna heartbeat + últimos 50 logs
    """
    user = request.user

    # Admins podem ver métricas de qualquer clínica
    if user.role != SupportUser.Role.ADMIN:
        has_access = ClinicAccess.objects.filter(
            support_user=user,
            clinic_id=clinic_id,
            revoked_at__isnull=True,
        ).exists()
        if not has_access:
            return Response({'error': 'forbidden'}, status=status.HTTP_403_FORBIDDEN)

    clinic = Clinic.objects.get(id=clinic_id)
    heartbeat = SystemHeartbeat.objects.get(clinic=clinic)
    logs = SystemLog.objects.filter(clinic=clinic).order_by('-occurred_at')[:50]

    return Response({
        'heartbeat': {
            'gateway_version': heartbeat.gateway_version,
            'db_size_mb': heartbeat.db_size_mb,
            'pending_sync': heartbeat.pending_sync,
            'last_seen': heartbeat.last_seen,
        },
        'logs': SystemLogSerializer(logs, many=True).data,
        'total_logs': SystemLog.objects.filter(clinic=clinic).count(),
    })
