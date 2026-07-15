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
            # BO-SEC-003: gateway_url precisa apontar para um destino público
            # (domínio/túnel exposto pelo Edge da clínica). `_is_safe_url`
            # bloqueia de propósito loopback/IPs privados (SSRF), então os
            # fixtures de teste não podem usar `localhost` — resolvemos o
            # hostname fake para um IP público via mock.
            gateway_url='http://edge.clinica-teste.com.br:8080',
        )

    @patch('clinics.services.socket.gethostbyname', return_value='203.0.113.10')
    @patch('httpx.Client.post')
    def test_sync_clinic_modules_success(self, mock_post, mock_dns):
        """Sucesso ao sincronizar módulos ativos com Edge."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = lambda: None

        result = sync_clinic_modules(self.clinic)
        self.assertTrue(result)
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], 'http://edge.clinica-teste.com.br:8080/api/v1/sync/modules')
        self.assertEqual(kwargs['json']['clinic_id'], str(self.clinic.id))
        self.assertEqual(kwargs['json']['modules'], ['dental'])

    @patch('clinics.services.socket.gethostbyname', return_value='203.0.113.10')
    @patch('httpx.Client.post')
    def test_sync_clinic_modules_failure(self, mock_post, mock_dns):
        """Erro de conexão ou HTTP falha graciosamente."""
        mock_post.side_effect = Exception("Connection refused")

        result = sync_clinic_modules(self.clinic)
        self.assertFalse(result)

    def test_sync_clinic_modules_blocks_private_gateway_url(self):
        """BO-SEC-003: gateway_url apontando para IP privado/loopback deve ser bloqueado (SSRF)."""
        self.clinic.gateway_url = 'http://localhost:8080'
        self.clinic.save(update_fields=['gateway_url'])

        with self.assertRaises(ValueError):
            sync_clinic_modules(self.clinic)

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
