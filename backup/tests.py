"""
EDGW-044 (Fase 1) — testes do app backup. Nenhuma chamada real à AWS: o
cliente S3 (`backup.services._s3_client`) é sempre mockado (ver
feedback_e2e_no_containers no tracker do SyncroHealth — E2E/CI nunca sobem
infra real como containers/localstack).
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework import status

from clinics.models import Clinic, ClinicStatus, Plan, ProvisioningStatus
from . import services


def make_clinic(name='Clínica Teste'):
    suffix = uuid.uuid4().hex[:8]
    return Clinic.objects.create(
        name=name,
        slug=f'clinica-{suffix}',
        plan=Plan.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj=f'{uuid.uuid4().int % 10**14:014d}',
        db_name=f'clinic_{suffix}',
        db_user=f'clinic_user_{suffix}',
        provisioning_status=ProvisioningStatus.PROVISIONED,
    )


VALID_OBJECT_KEY = '2026-08-06T02-00-00Z.sqlite.zst.aes256'


@override_settings(
    BACKUP_AWS_ACCESS_KEY='test-key',
    BACKUP_AWS_SECRET_KEY='test-secret',
    BACKUP_S3_BUCKET='test-bucket',
    BACKUP_AWS_REGION='us-east-1',
    BACKUP_PRESIGNED_TTL_SECONDS=900,
    BACKUP_RETENTION_DAYS=14,
)
class BackupServicesTests(TestCase):
    def setUp(self):
        self.clinic = make_clinic()

    def test_invalid_object_key_rejected_without_touching_s3(self):
        with patch.object(services, '_s3_client') as mock_client:
            with self.assertRaises(services.InvalidObjectKeyError):
                services.generate_upload_url(self.clinic, '../../etc/passwd')
            mock_client.assert_not_called()

    def test_upload_url_scoped_to_clinic_prefix(self):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = 'https://s3.example.com/presigned-put'
        with patch.object(services, '_s3_client', return_value=mock_s3):
            result = services.generate_upload_url(self.clinic, VALID_OBJECT_KEY)

        self.assertEqual(result['url'], 'https://s3.example.com/presigned-put')
        self.assertEqual(result['key'], f'clinic-{self.clinic.id}/{VALID_OBJECT_KEY}')

        call_kwargs = mock_s3.generate_presigned_url.call_args.kwargs
        self.assertEqual(call_kwargs['ClientMethod'], 'put_object')
        self.assertEqual(call_kwargs['Params']['Key'], f'clinic-{self.clinic.id}/{VALID_OBJECT_KEY}')
        self.assertEqual(call_kwargs['Params']['StorageClass'], 'STANDARD_IA')
        self.assertEqual(call_kwargs['ExpiresIn'], 900)

    def test_download_url_scoped_to_clinic_prefix(self):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = 'https://s3.example.com/presigned-get'
        with patch.object(services, '_s3_client', return_value=mock_s3):
            result = services.generate_download_url(self.clinic, VALID_OBJECT_KEY)

        self.assertEqual(result['url'], 'https://s3.example.com/presigned-get')
        call_kwargs = mock_s3.generate_presigned_url.call_args.kwargs
        self.assertEqual(call_kwargs['ClientMethod'], 'get_object')

    def test_list_backups_filters_and_sorts(self):
        mock_s3 = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [{
            'Contents': [
                {
                    'Key': f'clinic-{self.clinic.id}/2026-08-06T02-00-00Z.sqlite.zst.aes256',
                    'Size': 1024,
                    'LastModified': datetime(2026, 8, 6, 2, 0, tzinfo=timezone.utc),
                },
                {
                    'Key': f'clinic-{self.clinic.id}/2026-08-06T02-00-00Z.json',
                    'Size': 200,
                    'LastModified': datetime(2026, 8, 6, 2, 0, tzinfo=timezone.utc),
                },
                {
                    'Key': f'clinic-{self.clinic.id}/2026-07-30T02-00-00Z.sqlite.zst.aes256',
                    'Size': 900,
                    'LastModified': datetime(2026, 7, 30, 2, 0, tzinfo=timezone.utc),
                },
            ]
        }]
        mock_s3.get_paginator.return_value = mock_paginator

        with patch.object(services, '_s3_client', return_value=mock_s3):
            backups = services.list_backups(self.clinic)

        # .json (metadados internos, se algum dia existirem) nunca aparece na lista.
        self.assertEqual(len(backups), 2)
        # Mais recente primeiro.
        self.assertEqual(backups[0]['object_key'], '2026-08-06T02-00-00Z.sqlite.zst.aes256')
        self.assertEqual(backups[1]['object_key'], '2026-07-30T02-00-00Z.sqlite.zst.aes256')

    def test_purge_old_backups_deletes_only_past_retention(self):
        mock_s3 = MagicMock()
        mock_paginator = MagicMock()
        old_key = f'clinic-{self.clinic.id}/2020-01-01T02-00-00Z.sqlite.zst.aes256'
        recent_key = f'clinic-{self.clinic.id}/{VALID_OBJECT_KEY}'
        mock_paginator.paginate.return_value = [{
            'Contents': [
                {'Key': old_key, 'LastModified': datetime(2020, 1, 1, tzinfo=timezone.utc)},
                {'Key': recent_key, 'LastModified': datetime.now(timezone.utc)},
            ]
        }]
        mock_s3.get_paginator.return_value = mock_paginator

        with patch.object(services, '_s3_client', return_value=mock_s3):
            deleted = services.purge_old_backups(self.clinic, retention_days=14)

        self.assertEqual(deleted, 1)
        mock_s3.delete_object.assert_called_once_with(Bucket='test-bucket', Key=old_key)


@override_settings(
    BACKUP_AWS_ACCESS_KEY='test-key',
    BACKUP_AWS_SECRET_KEY='test-secret',
    BACKUP_S3_BUCKET='test-bucket',
)
class BackupViewsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.clinic = make_clinic()

    def test_presigned_url_requires_license_key(self):
        response = self.client.post(
            '/api/backup/presigned-url/',
            {'operation': 'put', 'object_key': VALID_OBJECT_KEY},
            format='json',
        )
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_presigned_url_rejects_invalid_operation(self):
        response = self.client.post(
            '/api/backup/presigned-url/',
            {'operation': 'delete', 'object_key': VALID_OBJECT_KEY},
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_presigned_url_rejects_invalid_object_key(self):
        response = self.client.post(
            '/api/backup/presigned-url/',
            {'operation': 'put', 'object_key': '../escape-attempt'},
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_presigned_url_success(self):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = 'https://s3.example.com/presigned-put'
        with patch.object(services, '_s3_client', return_value=mock_s3):
            response = self.client.post(
                '/api/backup/presigned-url/',
                {'operation': 'put', 'object_key': VALID_OBJECT_KEY},
                format='json',
                HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['url'], 'https://s3.example.com/presigned-put')
        self.assertEqual(response.data['key'], f'clinic-{self.clinic.id}/{VALID_OBJECT_KEY}')

    def test_presigned_url_never_leaks_aws_credentials_in_response(self):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = 'https://s3.example.com/presigned-put'
        with patch.object(services, '_s3_client', return_value=mock_s3):
            response = self.client.post(
                '/api/backup/presigned-url/',
                {'operation': 'put', 'object_key': VALID_OBJECT_KEY},
                format='json',
                HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
            )
        body = str(response.data)
        self.assertNotIn('test-key', body)
        self.assertNotIn('test-secret', body)

    def test_list_backups_requires_license_key(self):
        response = self.client.get('/api/backup/list/')
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_list_backups_success(self):
        mock_s3 = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [{'Contents': []}]
        mock_s3.get_paginator.return_value = mock_paginator
        with patch.object(services, '_s3_client', return_value=mock_s3):
            response = self.client.get(
                '/api/backup/list/',
                HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['backups'], [])

    def test_presigned_url_isolated_per_clinic(self):
        """Duas clínicas nunca compartilham prefixo — cada uma só pede/recebe URL escopada à própria."""
        other_clinic = make_clinic(name='Outra Clínica')
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = 'https://s3.example.com/presigned-put'

        with patch.object(services, '_s3_client', return_value=mock_s3):
            response = self.client.post(
                '/api/backup/presigned-url/',
                {'operation': 'put', 'object_key': VALID_OBJECT_KEY},
                format='json',
                HTTP_X_LICENSE_KEY=str(other_clinic.license_key),
            )

        self.assertEqual(response.data['key'], f'clinic-{other_clinic.id}/{VALID_OBJECT_KEY}')
        self.assertNotEqual(response.data['key'], f'clinic-{self.clinic.id}/{VALID_OBJECT_KEY}')
