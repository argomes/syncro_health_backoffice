"""
EDGW-044 (Fase 1) — emissão de URLs pré-assinadas S3 para o backup do
SQLite do gateway. Credenciais AWS reais ficam exclusivamente aqui
(backoffice) — o gateway nunca as recebe, só a URL temporária já escopada
ao prefixo da clínica autenticada (resolvida via X-License-Key, nunca
informada livremente pelo gateway).
"""
import re
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from django.conf import settings

# Formato esperado do object_key enviado pelo gateway: timestamp UTC no
# padrão do BackupWorker (ex: "2026-08-06T02-00-00Z") + sufixo fixo do
# pipeline de encriptação (EncryptionService, Go) — nunca aceitar um valor
# livre aqui, ou o gateway poderia (por bug ou compromisso) pedir uma URL
# pra fora do próprio prefixo de backup.
_OBJECT_KEY_RE = re.compile(
    r'^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z\.sqlite\.zst\.aes256$'
)


class InvalidObjectKeyError(ValueError):
    """object_key não bate com o formato esperado — recusa sem tocar S3."""


def _s3_client():
    return boto3.client(
        's3',
        aws_access_key_id=settings.BACKUP_AWS_ACCESS_KEY,
        aws_secret_access_key=settings.BACKUP_AWS_SECRET_KEY,
        region_name=settings.BACKUP_AWS_REGION,
        config=Config(signature_version='s3v4'),
    )


def _clinic_prefix(clinic) -> str:
    # clinic.id é UUID imutável (não usar license_key: existe pra rotacionar
    # no futuro, o id não).
    return f'clinic-{clinic.id}'


def generate_upload_url(clinic, object_key: str) -> dict:
    """Presigned PUT — TTL curto, escopado ao prefixo da clínica autenticada."""
    if not _OBJECT_KEY_RE.match(object_key):
        raise InvalidObjectKeyError(f'object_key fora do formato esperado: {object_key!r}')

    key = f'{_clinic_prefix(clinic)}/{object_key}'
    client = _s3_client()
    url = client.generate_presigned_url(
        ClientMethod='put_object',
        Params={
            'Bucket': settings.BACKUP_S3_BUCKET,
            'Key': key,
            'StorageClass': 'STANDARD_IA',
        },
        ExpiresIn=settings.BACKUP_PRESIGNED_TTL_SECONDS,
    )
    return {'url': url, 'key': key, 'expires_in': settings.BACKUP_PRESIGNED_TTL_SECONDS}


def generate_download_url(clinic, object_key: str) -> dict:
    """Presigned GET — mesmo escopo/TTL do upload, usado pelo restore manual."""
    if not _OBJECT_KEY_RE.match(object_key):
        raise InvalidObjectKeyError(f'object_key fora do formato esperado: {object_key!r}')

    key = f'{_clinic_prefix(clinic)}/{object_key}'
    client = _s3_client()
    url = client.generate_presigned_url(
        ClientMethod='get_object',
        Params={'Bucket': settings.BACKUP_S3_BUCKET, 'Key': key},
        ExpiresIn=settings.BACKUP_PRESIGNED_TTL_SECONDS,
    )
    return {'url': url, 'key': key, 'expires_in': settings.BACKUP_PRESIGNED_TTL_SECONDS}


def list_backups(clinic) -> list[dict]:
    """
    Lista os backups da clínica autenticada. Metadados apenas (sem URL —
    o operador pede a presigned GET separadamente pro item que quiser
    restaurar, evitando gerar dezenas de URLs válidas de uma vez).
    """
    client = _s3_client()
    prefix = f'{_clinic_prefix(clinic)}/'

    paginator = client.get_paginator('list_objects_v2')
    items = []
    for page in paginator.paginate(Bucket=settings.BACKUP_S3_BUCKET, Prefix=prefix):
        for obj in page.get('Contents', []):
            key = obj['Key']
            if not key.endswith('.sqlite.zst.aes256'):
                continue
            object_key = key[len(prefix):]
            items.append({
                'object_key': object_key,
                'size_bytes': obj['Size'],
                'last_modified': obj['LastModified'].astimezone(timezone.utc).isoformat(),
            })

    items.sort(key=lambda b: b['object_key'], reverse=True)
    return items


def purge_old_backups(clinic, retention_days: int) -> int:
    """
    Remove backups mais antigos que retention_days para uma clínica.
    Usado pelo management command `purge_old_backups` (cron externo, mesmo
    padrão de `tiss/management/commands/purgar_operator_call_log.py`).
    Retorna a quantidade removida.
    """
    client = _s3_client()
    prefix = f'{_clinic_prefix(clinic)}/'
    cutoff = datetime.now(timezone.utc).timestamp() - (retention_days * 86400)

    paginator = client.get_paginator('list_objects_v2')
    deleted = 0
    for page in paginator.paginate(Bucket=settings.BACKUP_S3_BUCKET, Prefix=prefix):
        for obj in page.get('Contents', []):
            if obj['LastModified'].timestamp() < cutoff:
                client.delete_object(Bucket=settings.BACKUP_S3_BUCKET, Key=obj['Key'])
                deleted += 1
    return deleted
