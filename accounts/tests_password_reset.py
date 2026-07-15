"""
TASK-BO-12 — fluxo de "esqueci minha senha", ponta a ponta, pra SupportUser
(views built-in do Django puras) e ClinicUser (accounts/password_reset_clinic.py).

Usa o backend de teste do Django (django.core.mail.outbox) — não bate no
ZeptoMail real. O teste ponta a ponta contra o provedor real fica pendente
até haver credencial de produção + DNS (SPF/DKIM) configurados, conforme
combinado no escopo da TASK-BO-12.
"""
import re
import uuid

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from clinics.models import Clinic, Plan, ClinicStatus
from .models import ClinicUser

SupportUser = get_user_model()


def make_clinic(name='Clínica Teste'):
    return Clinic.objects.create(
        name=name,
        slug=f'clinica-{uuid.uuid4().hex[:8]}',
        plan=Plan.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
        db_name=f'clinic_{uuid.uuid4().hex[:8]}',
        db_user=f'u_{uuid.uuid4().hex[:8]}',
    )


def _extract_reset_path(email_body):
    """Extrai o path do link de reset (/admin-password-reset/confirm/<uid>/<token>/
    ou /portal/password-reset/confirm/<uid>/<token>/) do corpo do e-mail de teste."""
    match = re.search(r'https?://[^/]+(/\S+/)', email_body)
    assert match, f'Link de reset não encontrado no corpo do e-mail: {email_body!r}'
    return match.group(1)


class SupportUserPasswordResetFlowTest(TestCase):
    """SupportUser é o AUTH_USER_MODEL — reset via views built-in puras do Django."""

    def setUp(self):
        self.user = SupportUser.objects.create_user(
            username='suporte',
            email='suporte@syncro.com',
            password='senha-antiga-123',
            role='support',
        )

    def test_reset_flow_end_to_end(self):
        # 1. Solicita o reset.
        response = self.client.post(
            reverse('admin_password_reset'),
            {'email': 'suporte@syncro.com'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('admin_password_reset_done'))

        # 2. Email cai na outbox de teste — sem PHI, só nome/username + link.
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertIn('suporte@syncro.com', sent.to)
        self.assertNotIn('paciente', sent.body.lower())
        self.assertNotIn('cpf', sent.body.lower())

        # HTML alternativo também foi anexado (texto + HTML mínimo).
        self.assertEqual(len(sent.alternatives), 1)
        html_body, mimetype = sent.alternatives[0]
        self.assertEqual(mimetype, 'text/html')
        self.assertIn('Redefinir minha senha', html_body)

        reset_path = _extract_reset_path(sent.body)

        # 3. Segue o link — GET redireciona pra URL "set-password" (token some da URL).
        confirm_response = self.client.get(reset_path)
        self.assertEqual(confirm_response.status_code, 302)
        set_password_response = self.client.get(confirm_response.url)
        self.assertEqual(set_password_response.status_code, 200)
        self.assertTrue(set_password_response.context['validlink'])

        # 4. Envia a nova senha.
        post_response = self.client.post(
            confirm_response.url,
            {'new_password1': 'senha-nova-muito-forte-456', 'new_password2': 'senha-nova-muito-forte-456'},
        )
        self.assertEqual(post_response.status_code, 302)
        self.assertRedirects(post_response, reverse('admin_password_reset_complete'))

        # 5. Login com a senha antiga falha, com a nova funciona.
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password('senha-antiga-123'))
        self.assertTrue(self.user.check_password('senha-nova-muito-forte-456'))

    def test_unknown_email_does_not_leak_account_existence(self):
        response = self.client.post(
            reverse('admin_password_reset'),
            {'email': 'nao-existe@syncro.com'},
        )
        # Django sempre redireciona pra done, exista ou não a conta — não
        # deve vazar se um e-mail está cadastrado.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)


class ClinicUserPasswordResetFlowTest(TestCase):
    """ClinicUser não é AUTH_USER_MODEL — reset via accounts/password_reset_clinic.py."""

    def setUp(self):
        self.clinic = make_clinic()
        self.user = ClinicUser(clinic=self.clinic, email='gestor@clinica.com', name='Gestora da Clínica')
        self.user.set_password('senha-antiga-clinica-123')
        self.user.save()

    def test_reset_flow_end_to_end(self):
        response = self.client.post(
            reverse('portal_password_reset'),
            {'email': 'gestor@clinica.com'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('portal_password_reset_done'))

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertIn('gestor@clinica.com', sent.to)
        self.assertIn('Gestora da Clínica', sent.body)
        self.assertNotIn('paciente', sent.body.lower())
        self.assertNotIn('cpf', sent.body.lower())

        self.assertEqual(len(sent.alternatives), 1)
        html_body, mimetype = sent.alternatives[0]
        self.assertEqual(mimetype, 'text/html')

        reset_path = _extract_reset_path(sent.body)

        confirm_response = self.client.get(reset_path)
        self.assertEqual(confirm_response.status_code, 302)
        set_password_response = self.client.get(confirm_response.url)
        self.assertEqual(set_password_response.status_code, 200)
        self.assertTrue(set_password_response.context['validlink'])

        post_response = self.client.post(
            confirm_response.url,
            {'new_password1': 'senha-nova-clinica-muito-forte-456', 'new_password2': 'senha-nova-clinica-muito-forte-456'},
        )
        self.assertEqual(post_response.status_code, 302)
        self.assertRedirects(post_response, reverse('portal_password_reset_complete'))

        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password('senha-antiga-clinica-123'))
        self.assertTrue(self.user.check_password('senha-nova-clinica-muito-forte-456'))

    def test_unknown_email_does_not_leak_account_existence(self):
        response = self.client.post(
            reverse('portal_password_reset'),
            {'email': 'nao-existe@clinica.com'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)

    def test_ldap_provider_never_receives_local_reset_email(self):
        """
        ClinicUser.AuthProvider.LDAP não tem senha gerenciada por este sistema
        (has_usable_password() retorna False) — reset local não deve mandar
        e-mail incentivando o usuário a definir uma senha que o login LDAP
        vai ignorar (ClinicUser.check_password sempre retorna False pra LDAP).
        """
        ldap_user = ClinicUser(
            clinic=self.clinic,
            email='ldap@clinica.com',
            name='Usuária LDAP',
            auth_provider=ClinicUser.AuthProvider.LDAP,
        )
        ldap_user.save()

        response = self.client.post(
            reverse('portal_password_reset'),
            {'email': 'ldap@clinica.com'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)

    def test_inactive_clinic_user_does_not_receive_reset_email(self):
        self.user.is_active = False
        self.user.save()

        response = self.client.post(
            reverse('portal_password_reset'),
            {'email': 'gestor@clinica.com'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)

    def test_cross_clinic_isolation_two_users_same_email_different_clinics(self):
        """
        (clinic, email) é unique_together, não email sozinho — dois
        ClinicUsers de clínicas diferentes podem ter o mesmo e-mail sem
        colidir. O reset deve mandar um e-mail por conta encontrada, cada
        um com um token que só vale para aquele usuário/pk específico.
        """
        other_clinic = make_clinic(name='Outra Clínica')
        other_user = ClinicUser(clinic=other_clinic, email='gestor@clinica.com', name='Gestor Outra Clínica')
        other_user.set_password('outra-senha-123')
        other_user.save()

        response = self.client.post(
            reverse('portal_password_reset'),
            {'email': 'gestor@clinica.com'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 2)

        # O token do primeiro e-mail não deve validar o segundo usuário e vice-versa.
        reset_path_1 = _extract_reset_path(mail.outbox[0].body)
        reset_path_2 = _extract_reset_path(mail.outbox[1].body)
        self.assertNotEqual(reset_path_1, reset_path_2)
