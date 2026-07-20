"""
Testes de BACFF-AVULSA-04 — clinics/permissions.py.

`IsAuthenticatedByLicenseKey` e `IsAuthenticatedByAnyLicenseKey` capturavam
`(Clinic.DoesNotExist, Exception)` no mesmo except, tratando qualquer erro
inesperado (DB fora do ar, bug de query) como se fosse uma license_key
inválida — mascarando instabilidade de infra como tentativa de acesso
inválida nos logs. Agora:
- `Clinic.DoesNotExist` continua retornando False silenciosamente (fluxo
  normal de auth inválida).
- Qualquer outra `Exception` é logada (logger.exception, com stack trace)
  e ainda retorna False (fail-closed), sem expor detalhe técnico ao cliente.
"""
import logging
import uuid
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from .models import Clinic, ClinicStatus, Plan, ProvisioningStatus
from .permissions import IsAuthenticatedByLicenseKey, IsAuthenticatedByAnyLicenseKey


def make_clinic(clinic_status=ClinicStatus.ACTIVE, **kwargs):
    return Clinic.objects.create(
        name='Clínica Teste Permissions',
        slug=f'clinica-perm-{uuid.uuid4().hex[:8]}',
        plan=Plan.PROFESSIONAL,
        status=clinic_status,
        cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
        db_name=f'clinic_{uuid.uuid4().hex[:8]}',
        db_user=f'u_{uuid.uuid4().hex[:8]}',
        provisioning_status=ProvisioningStatus.PROVISIONED,
        **kwargs,
    )


class PermissionClassesExceptionHandlingTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _request(self, license_key='alguma-chave'):
        request = self.factory.get('/qualquer-rota/')
        request.META['HTTP_X_LICENSE_KEY'] = license_key
        return request

    # ── IsAuthenticatedByLicenseKey ────────────────────────────────────────

    def test_license_key_ausente_retorna_false_sem_tocar_db(self):
        request = self.factory.get('/qualquer-rota/')
        permission = IsAuthenticatedByLicenseKey()
        self.assertFalse(permission.has_permission(request, None))

    def test_clinic_does_not_exist_retorna_false_e_nao_loga_erro(self):
        request = self._request(license_key=str(uuid.uuid4()))
        permission = IsAuthenticatedByLicenseKey()
        with patch('clinics.permissions.logger.exception') as mock_log:
            self.assertFalse(permission.has_permission(request, None))
        mock_log.assert_not_called()

    def test_clinic_inativa_nao_e_encontrada_por_status_activo_retorna_false(self):
        clinic = make_clinic(clinic_status=ClinicStatus.SUSPENDED)
        request = self._request(license_key=str(clinic.license_key))
        permission = IsAuthenticatedByLicenseKey()
        with patch('clinics.permissions.logger.exception') as mock_log:
            self.assertFalse(permission.has_permission(request, None))
        mock_log.assert_not_called()

    def test_erro_inesperado_do_db_e_logado_e_retorna_false(self):
        request = self._request(license_key=str(uuid.uuid4()))
        permission = IsAuthenticatedByLicenseKey()
        with patch('clinics.permissions.Clinic.objects.get', side_effect=RuntimeError('conexão com o banco caiu')):
            with patch('clinics.permissions.logger.exception') as mock_log:
                self.assertFalse(permission.has_permission(request, None))
            mock_log.assert_called_once()

    def test_clinic_valida_retorna_true_e_injeta_request_clinic(self):
        clinic = make_clinic()
        request = self._request(license_key=str(clinic.license_key))
        permission = IsAuthenticatedByLicenseKey()
        self.assertTrue(permission.has_permission(request, None))
        self.assertEqual(request.clinic, clinic)

    # ── IsAuthenticatedByAnyLicenseKey ──────────────────────────────────────

    def test_any_clinic_does_not_exist_retorna_false_e_nao_loga_erro(self):
        request = self._request(license_key=str(uuid.uuid4()))
        permission = IsAuthenticatedByAnyLicenseKey()
        with patch('clinics.permissions.logger.exception') as mock_log:
            self.assertFalse(permission.has_permission(request, None))
        mock_log.assert_not_called()

    def test_any_erro_inesperado_do_db_e_logado_e_retorna_false(self):
        request = self._request(license_key=str(uuid.uuid4()))
        permission = IsAuthenticatedByAnyLicenseKey()
        with patch('clinics.permissions.Clinic.objects.get', side_effect=RuntimeError('conexão com o banco caiu')):
            with patch('clinics.permissions.logger.exception') as mock_log:
                self.assertFalse(permission.has_permission(request, None))
            mock_log.assert_called_once()

    def test_any_aceita_clinica_em_qualquer_status(self):
        clinic = make_clinic(clinic_status=ClinicStatus.SUSPENDED)
        request = self._request(license_key=str(clinic.license_key))
        permission = IsAuthenticatedByAnyLicenseKey()
        self.assertTrue(permission.has_permission(request, None))
        self.assertEqual(request.clinic, clinic)
