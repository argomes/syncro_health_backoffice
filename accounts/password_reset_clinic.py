"""
TASK-BO-12 — fluxo de "esqueci minha senha" para ClinicUser.

ClinicUser não é AUTH_USER_MODEL (Django só permite um por projeto, e esse
posto já é do SupportUser — ver accounts/models.py). Por isso as views
built-in do Django (django.contrib.auth.views.PasswordReset*) não servem
puras aqui: elas resolvem o usuário via `django.contrib.auth.get_user_model()`
hardcoded no módulo, então qualquer subclasse ainda tentaria buscar
SupportUser.

O que É reaproveitado do Django (não reescrito do zero):
- `default_token_generator` (django.contrib.auth.tokens) — geração/validação
  de token com expiração (PASSWORD_RESET_TIMEOUT) e invalidação automática
  quando a senha muda. É genérico o bastante pra funcionar com qualquer
  objeto que tenha pk/password/last_login/email, não exige AbstractBaseUser.
- `SetPasswordForm` (django.contrib.auth.forms) — valida a nova senha
  (validadores de AUTH_PASSWORD_VALIDATORS) e chama user.set_password() +
  user.save(), que ClinicUser já implementa com a mesma assinatura.
- `PasswordResetConfirmView`/`PasswordResetView` do Django — herdadas, só
  sobrescrevendo o ponto de resolução do usuário (get_user/get_users), que é
  exatamente o ponto de extensão que o próprio Django documenta pra esse
  cenário de "segundo modelo de usuário".

O que é próprio: só a consulta a ClinicUser em vez de get_user_model(), e o
encode/decode do uid (mesmo padrão urlsafe_base64, adaptado pra pk UUID).
"""
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import PasswordResetConfirmView, PasswordResetView
from django.contrib.sites.shortcuts import get_current_site
from django.core.exceptions import ValidationError
from django.urls import reverse_lazy
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from .models import ClinicUser


class ClinicPasswordResetForm(PasswordResetForm):
    """
    Mesmo formulário/fluxo do PasswordResetForm do Django, mas resolvendo
    contra ClinicUser em vez de get_user_model() (que aqui é SupportUser).

    LGPD: o e-mail de reset carrega só nome + link — nenhum dado de
    paciente. ClinicUser é usuário administrativo da clínica (gestor do
    portal), não paciente; mesmo assim o corpo do e-mail (templates
    portal_gestor/password_reset_email.*) não referencia nenhum dado clínico.
    """

    def get_users(self, email):
        active_users = ClinicUser.objects.filter(email__iexact=email, is_active=True)
        return (
            u for u in active_users
            if u.has_usable_password() and u.email.lower() == email.lower()
        )

    def save(
        self,
        domain_override=None,
        subject_template_name='portal_gestor/password_reset_subject.txt',
        email_template_name='portal_gestor/password_reset_email.txt',
        use_https=False,
        token_generator=default_token_generator,
        from_email=None,
        request=None,
        html_email_template_name='portal_gestor/password_reset_email.html',
        extra_email_context=None,
    ):
        email = self.cleaned_data['email']
        if not domain_override:
            current_site = get_current_site(request)
            site_name = current_site.name
            domain = current_site.domain
        else:
            site_name = domain = domain_override

        for user in self.get_users(email):
            uid = urlsafe_base64_encode(force_bytes(str(user.pk)))
            context = {
                'email': user.email,
                'domain': domain,
                'site_name': site_name,
                'uid': uid,
                'user': user,
                'token': token_generator.make_token(user),
                'protocol': 'https' if use_https else 'http',
                **(extra_email_context or {}),
            }
            self.send_mail(
                subject_template_name,
                email_template_name,
                context,
                from_email,
                user.email,
                html_email_template_name=html_email_template_name,
            )


class ClinicPasswordResetView(PasswordResetView):
    form_class = ClinicPasswordResetForm
    template_name = 'portal_gestor/password_reset_form.html'
    email_template_name = 'portal_gestor/password_reset_email.txt'
    html_email_template_name = 'portal_gestor/password_reset_email.html'
    subject_template_name = 'portal_gestor/password_reset_subject.txt'
    success_url = reverse_lazy('portal_password_reset_done')


class ClinicPasswordResetConfirmView(PasswordResetConfirmView):
    form_class = SetPasswordForm
    template_name = 'portal_gestor/password_reset_confirm.html'
    success_url = reverse_lazy('portal_password_reset_complete')

    def get_user(self, uidb64):
        try:
            uid = urlsafe_base64_decode(uidb64).decode()
            user = ClinicUser.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, ClinicUser.DoesNotExist, ValidationError):
            user = None
        return user
