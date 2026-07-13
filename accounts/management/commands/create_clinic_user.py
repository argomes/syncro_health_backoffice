"""
Cria (ou atualiza a senha de) um ClinicUser — admin/gerente de uma clínica que
loga no portal_gestor. Não existe fluxo de auto-cadastro ainda (a intenção de
produto é criar isso junto do onboarding da clínica); até lá, este comando é o
caminho local/manual, no lugar de `manage.py shell` avulso.

Uso:
    python manage.py create_clinic_user --clinic-slug clinica-demo --email admin@demo.com
    python manage.py create_clinic_user --clinic-id <uuid> --email admin@demo.com --name "Admin Demo"
    python manage.py create_clinic_user --clinic-slug clinica-demo --email admin@demo.com --password demo1234 --noinput

Sem --password, pede a senha interativamente (getpass, não aparece no
terminal nem fica no histórico do shell) — mesmo padrão do `createsuperuser`
nativo do Django. Se o ClinicUser já existir para essa clínica+email, o
comando atualiza a senha em vez de falhar (idempotente, útil para resetar
senha de teste local).
"""
import getpass
import sys

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from accounts.models import ClinicUser
from clinics.models import Clinic


class Command(BaseCommand):
    help = 'Cria ou atualiza a senha de um ClinicUser (admin/gerente de clínica) para testes locais.'

    def add_arguments(self, parser):
        clinic_group = parser.add_mutually_exclusive_group(required=True)
        clinic_group.add_argument('--clinic-slug', help='Slug da clínica já existente.')
        clinic_group.add_argument('--clinic-id', help='UUID da clínica já existente.')

        parser.add_argument('--email', required=True, help='E-mail de login do ClinicUser.')
        parser.add_argument('--name', default='', help='Nome de exibição (default: parte local do e-mail).')
        parser.add_argument('--password', default=None, help='Senha em texto — se omitida, pede interativamente.')
        parser.add_argument(
            '--noinput', '--no-input', action='store_true', dest='noinput',
            help='Não pede senha interativamente — falha se --password não for informado.',
        )

    def handle(self, *args, **options):
        clinic = self._resolve_clinic(options)
        email = options['email'].strip().lower()
        name = options['name'].strip() or email.split('@')[0]

        password = options['password']
        if password is None:
            if options['noinput']:
                raise CommandError('--password é obrigatório junto com --noinput.')
            password = self._prompt_password()

        user, created = ClinicUser.objects.get_or_create(
            clinic=clinic, email=email, defaults={'name': name},
        )
        if not created and name:
            user.name = name
        user.set_password(password)
        user.is_active = True
        user.save()

        action = 'criado' if created else 'atualizado (senha redefinida)'
        self.stdout.write(self.style.SUCCESS(
            f'ClinicUser {action}: {email} — clínica "{clinic.name}" (id={clinic.id})'
        ))

    def _resolve_clinic(self, options):
        try:
            if options['clinic_slug']:
                return Clinic.objects.get(slug=options['clinic_slug'])
            return Clinic.objects.get(id=options['clinic_id'])
        except Clinic.DoesNotExist as exc:
            raise CommandError('Clínica não encontrada com o slug/id informado.') from exc
        except (ValueError, ValidationError) as exc:
            raise CommandError(f'clinic-id inválido: {exc}') from exc

    def _prompt_password(self):
        if not sys.stdin.isatty():
            raise CommandError(
                'Sem terminal interativo para pedir a senha — use --password ou --noinput com --password.'
            )
        while True:
            password = getpass.getpass('Senha: ')
            confirm = getpass.getpass('Confirme a senha: ')
            if password != confirm:
                self.stderr.write(self.style.ERROR('As senhas não coincidem. Tente de novo.'))
                continue
            if len(password) < 8:
                self.stderr.write(self.style.ERROR('A senha precisa ter ao menos 8 caracteres.'))
                continue
            return password
