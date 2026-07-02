from unittest.mock import patch
from django.test import TestCase
from clinics.models import Clinic, ClinicStatus, Plan
from clinics.services import sync_clinic_modules


class ClinicModulesTests(TestCase):
    def setUp(self):
        self.clinic = Clinic.objects.create(
            name='Clínica Modular',
            slug='clinic-mod',
            plan=Plan.STARTER,
            status=ClinicStatus.ACTIVE,
            active_modules=['dental'],
            gateway_url='http://localhost:8080',
        )

    @patch('httpx.Client.post')
    def test_sync_clinic_modules_success(self, mock_post):
        """Sucesso ao sincronizar módulos ativos com Edge."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None

        result = sync_clinic_modules(self.clinic)
        self.assertTrue(result)
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], 'http://localhost:8080/api/v1/sync/modules')
        self.assertEqual(kwargs['json']['clinic_id'], str(self.clinic.id))
        self.assertEqual(kwargs['json']['modules'], ['dental'])

    @patch('httpx.Client.post')
    def test_sync_clinic_modules_failure(self, mock_post):
        """Erro de conexão ou HTTP falha graciosamente."""
        mock_post.side_effect = Exception("Connection refused")

        result = sync_clinic_modules(self.clinic)
        self.assertFalse(result)

    @patch('clinics.signals.sync_modules_to_edge')
    def test_signal_triggers_on_save(self, mock_task):
        """Sinal post_save enfileira sync_modules_to_edge via .delay() quando gateway_url existir."""
        self.clinic.active_modules = ['dental', 'ginecologia']
        self.clinic.save()
        mock_task.delay.assert_called_once_with(str(self.clinic.pk))

    @patch('clinics.signals.sync_modules_to_edge')
    def test_signal_ignored_on_provisioning_save(self, mock_task):
        """Ignora sinal se campos de salvamento forem só de provisionamento."""
        self.clinic.save(update_fields=['provisioning_status', 'provisioning_error'])
        mock_task.delay.assert_not_called()
