from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from clinics.models import Clinic
from clinics.permissions import IsAuthenticatedByLicenseKey
from metrics.serializers import SystemLogSerializer
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
    return Response({'ok': True}, status=status.HTTP_200_OK)


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
def dashboard_metrics(request, clinic_id):
    """
    GET /api/metrics/dashboard/{clinic_id}
    Retorna heartbeat + últimos 50 logs
    """
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
