import logging

from botocore.exceptions import ClientError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from clinics.permissions import IsAuthenticatedByLicenseKey

from . import services

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([IsAuthenticatedByLicenseKey])
def presigned_url(request):
    """
    EDGW-044 — emite uma URL S3 pré-assinada (PUT ou GET) escopada ao
    prefixo da clínica autenticada via X-License-Key. O gateway nunca
    recebe credenciais AWS — só esta URL, com TTL curto.

    Body: {"operation": "put"|"get", "object_key": "<timestamp>.sqlite.zst.aes256"}
    """
    clinic = request.clinic
    operation = request.data.get('operation')
    object_key = request.data.get('object_key', '')

    if operation not in ('put', 'get'):
        return Response(
            {'error': 'operation deve ser "put" ou "get"'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        if operation == 'put':
            result = services.generate_upload_url(clinic, object_key)
        else:
            result = services.generate_download_url(clinic, object_key)
    except services.InvalidObjectKeyError as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except ClientError:
        logger.exception('[Backup] Falha ao gerar presigned URL para clinic_id=%s', clinic.id)
        return Response(
            {'error': 'falha ao gerar URL de backup — tente novamente'},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    return Response(result, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticatedByLicenseKey])
def list_backups(request):
    """Lista os backups disponíveis (metadados apenas) da clínica autenticada."""
    clinic = request.clinic
    try:
        backups = services.list_backups(clinic)
    except ClientError:
        logger.exception('[Backup] Falha ao listar backups para clinic_id=%s', clinic.id)
        return Response(
            {'error': 'falha ao listar backups — tente novamente'},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    return Response({'backups': backups}, status=status.HTTP_200_OK)
