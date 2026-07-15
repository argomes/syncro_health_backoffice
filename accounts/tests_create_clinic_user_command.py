"""
Testes do management command `create_clinic_user` — cria/atualiza ClinicUser
para uso local (dev/QA), enquanto não existe fluxo de auto-cadastro no
onboarding real da clínica.
"""
import uuid
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from clinics.models import Clinic, ClinicStatus, Plan

from .models import ClinicUser


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


class CreateClinicUserCommandTest(TestCase):
    def setUp(self):
        self.clinic = make_clinic()

    def _call(self, **kwargs):
        out = StringIO()
        call_command('create_clinic_user', stdout=out, **kwargs)
        return out.getvalue()

    def test_creates_user_by_slug(self):
        output = self._call(clinic_slug=self.clinic.slug, email='novo@a.com', password='senha1234', noinput=True)
        user = ClinicUser.objects.get(clinic=self.clinic, email='novo@a.com')
        self.assertTrue(user.check_password('senha1234'))
        self.assertTrue(user.is_active)
        self.assertIn('criado', output)

    def test_creates_user_by_clinic_id(self):
        self._call(clinic_id=str(self.clinic.id), email='novo@a.com', password='senha1234', noinput=True)
        self.assertTrue(ClinicUser.objects.filter(clinic=self.clinic, email='novo@a.com').exists())

    def test_default_name_derived_from_email(self):
        self._call(clinic_slug=self.clinic.slug, email='fulano@a.com', password='senha1234', noinput=True)
        user = ClinicUser.objects.get(clinic=self.clinic, email='fulano@a.com')
        self.assertEqual(user.name, 'fulano')

    def test_rerun_updates_password_idempotently(self):
        self._call(clinic_slug=self.clinic.slug, email='a@a.com', password='senha-antiga', noinput=True)
        output = self._call(clinic_slug=self.clinic.slug, email='a@a.com', password='senha-nova', noinput=True)

        user = ClinicUser.objects.get(clinic=self.clinic, email='a@a.com')
        self.assertTrue(user.check_password('senha-nova'))
        self.assertFalse(user.check_password('senha-antiga'))
        self.assertEqual(ClinicUser.objects.filter(clinic=self.clinic, email='a@a.com').count(), 1)
        self.assertIn('atualizado', output)

    def test_reactivates_inactive_user_on_rerun(self):
        self._call(clinic_slug=self.clinic.slug, email='a@a.com', password='senha1234', noinput=True)
        user = ClinicUser.objects.get(clinic=self.clinic, email='a@a.com')
        user.is_active = False
        user.save(update_fields=['is_active'])

        self._call(clinic_slug=self.clinic.slug, email='a@a.com', password='senha1234', noinput=True)
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_email_normalized_lowercase(self):
        self._call(clinic_slug=self.clinic.slug, email='MAIUSCULO@A.COM', password='senha1234', noinput=True)
        self.assertTrue(ClinicUser.objects.filter(clinic=self.clinic, email='maiusculo@a.com').exists())

    def test_missing_clinic_raises_clear_error(self):
        with self.assertRaises(CommandError):
            self._call(clinic_slug='nao-existe', email='a@a.com', password='senha1234', noinput=True)
        self.assertFalse(ClinicUser.objects.exists())

    def test_invalid_clinic_id_raises_clear_error_not_crash(self):
        with self.assertRaises(CommandError):
            self._call(clinic_id='not-a-uuid', email='a@a.com', password='senha1234', noinput=True)

    def test_noinput_without_password_raises_clear_error(self):
        with self.assertRaises(CommandError):
            self._call(clinic_slug=self.clinic.slug, email='a@a.com', noinput=True)
        self.assertFalse(ClinicUser.objects.exists())

    def test_same_email_different_clinics_creates_separate_users(self):
        other_clinic = make_clinic('Outra Clínica')
        self._call(clinic_slug=self.clinic.slug, email='mesmo@a.com', password='senha1234', noinput=True)
        self._call(clinic_slug=other_clinic.slug, email='mesmo@a.com', password='senha5678', noinput=True)

        self.assertEqual(ClinicUser.objects.filter(email='mesmo@a.com').count(), 2)
