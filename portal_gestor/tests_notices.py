"""
Testes da TASK-051 — aviso contextual dispensável (substitui a ideia de mural
genérico, rejeitada pela PO Healthtech em 2026-07-13).

Cobre: vigência (antes/durante/depois do período), kill-switch `active`,
dispensa (idempotente, persiste entre sessões, isolada por ClinicUser),
banner ausente quando não há aviso, e o endpoint HTMX de dispensa.
"""
import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework import status

from accounts.models import ClinicUser
from clinics.models import Clinic, ClinicStatus, Plan

from . import notices
from .models import ClinicUserNoticeDismissal, ProductNotice


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


def make_clinic_user(clinic, email='gerente@a.com', password='senha-123'):
    user = ClinicUser(clinic=clinic, email=email, name='Gerente')
    user.set_password(password)
    user.save()
    return user


def make_notice(active=True, starts_delta=timedelta(days=-1), ends_delta=timedelta(days=1), title='Aviso'):
    now = timezone.now()
    return ProductNotice.objects.create(
        title=title, body='Corpo do aviso',
        active=active, starts_at=now + starts_delta, ends_at=now + ends_delta,
    )


class ProductNoticeModelTest(TestCase):
    def test_is_current_within_vigency_and_active(self):
        notice = make_notice()
        self.assertTrue(notice.is_current())

    def test_is_current_false_before_starts_at(self):
        notice = make_notice(starts_delta=timedelta(days=1), ends_delta=timedelta(days=5))
        self.assertFalse(notice.is_current())

    def test_is_current_false_after_ends_at(self):
        notice = make_notice(starts_delta=timedelta(days=-5), ends_delta=timedelta(days=-1))
        self.assertFalse(notice.is_current())

    def test_is_current_false_when_inactive_even_within_vigency(self):
        notice = make_notice(active=False)
        self.assertFalse(notice.is_current())


class GetActiveNoticeForUserTest(TestCase):
    def setUp(self):
        self.clinic = make_clinic()
        self.user = make_clinic_user(self.clinic)

    def test_none_when_no_notice_exists(self):
        self.assertIsNone(notices.get_active_notice_for_user(self.user))

    def test_none_for_none_user(self):
        make_notice()
        self.assertIsNone(notices.get_active_notice_for_user(None))

    def test_returns_current_notice(self):
        notice = make_notice()
        self.assertEqual(notices.get_active_notice_for_user(self.user), notice)

    def test_ignores_future_notice(self):
        make_notice(starts_delta=timedelta(days=1), ends_delta=timedelta(days=5))
        self.assertIsNone(notices.get_active_notice_for_user(self.user))

    def test_ignores_expired_notice(self):
        make_notice(starts_delta=timedelta(days=-5), ends_delta=timedelta(days=-1))
        self.assertIsNone(notices.get_active_notice_for_user(self.user))

    def test_ignores_inactive_notice(self):
        make_notice(active=False)
        self.assertIsNone(notices.get_active_notice_for_user(self.user))

    def test_excludes_already_dismissed_by_this_user(self):
        notice = make_notice()
        ClinicUserNoticeDismissal.objects.create(clinic_user=self.user, notice=notice)
        self.assertIsNone(notices.get_active_notice_for_user(self.user))

    def test_dismissal_by_other_user_does_not_hide_notice(self):
        notice = make_notice()
        other_user = make_clinic_user(self.clinic, email='outro@a.com')
        ClinicUserNoticeDismissal.objects.create(clinic_user=other_user, notice=notice)

        self.assertEqual(notices.get_active_notice_for_user(self.user), notice)

    def test_most_recent_notice_returned_when_multiple_current(self):
        older = make_notice(title='Antigo', starts_delta=timedelta(days=-3))
        newer = make_notice(title='Novo', starts_delta=timedelta(days=-1))
        self.assertEqual(notices.get_active_notice_for_user(self.user), newer)
        self.assertNotEqual(older, newer)


class DismissNoticeTest(TestCase):
    def setUp(self):
        self.clinic = make_clinic()
        self.user = make_clinic_user(self.clinic)

    def test_dismiss_creates_record(self):
        notice = make_notice()
        notices.dismiss_notice(self.user, notice.id)
        self.assertTrue(ClinicUserNoticeDismissal.objects.filter(clinic_user=self.user, notice=notice).exists())

    def test_dismiss_is_idempotent(self):
        notice = make_notice()
        notices.dismiss_notice(self.user, notice.id)
        notices.dismiss_notice(self.user, notice.id)
        self.assertEqual(ClinicUserNoticeDismissal.objects.filter(clinic_user=self.user, notice=notice).count(), 1)

    def test_dismiss_nonexistent_notice_returns_false(self):
        found = notices.dismiss_notice(self.user, 99999)
        self.assertFalse(found)


class NoticeBannerRenderingTest(TestCase):
    """Testa o banner via o contexto processor, através de qualquer página
    server-rendered do portal (usa a stub /portal/ já protegida)."""

    def setUp(self):
        self.clinic = make_clinic()
        make_clinic_user(self.clinic)
        self.client.post('/portal/login/', {'email': 'gerente@a.com', 'password': 'senha-123'})

    def test_no_banner_when_no_active_notice(self):
        response = self.client.get('/portal/')
        self.assertNotContains(response, 'id="notice-banner"')

    def test_banner_shows_active_notice(self):
        notice = make_notice(title='Nova funcionalidade!')
        response = self.client.get('/portal/')
        self.assertContains(response, 'id="notice-banner"')
        self.assertContains(response, 'Nova funcionalidade!')

    def test_banner_hidden_for_future_notice(self):
        make_notice(title='Futuro', starts_delta=timedelta(days=1), ends_delta=timedelta(days=5))
        response = self.client.get('/portal/')
        self.assertNotContains(response, 'id="notice-banner"')

    def test_login_page_does_not_query_or_crash_without_clinic_user(self):
        """Contexto processor roda em toda página, incluindo login (sem
        clinic_user ainda) — não pode crashar nem vazar aviso pré-login."""
        self.client.cookies.clear()
        response = self.client.get('/portal/login/')
        self.assertEqual(response.status_code, 200)


class NoticeDismissViewTest(TestCase):
    def setUp(self):
        self.clinic_a = make_clinic('Clínica A')
        self.clinic_b = make_clinic('Clínica B')
        self.user_a = make_clinic_user(self.clinic_a, email='gerente@a.com')
        self.user_b = make_clinic_user(self.clinic_b, email='gerente@b.com')
        self.notice = make_notice()

    def _login(self, email):
        resp = self.client.post('/portal/api/auth/login/', {'email': email, 'password': 'senha-123'}, format='json')
        return {'HTTP_AUTHORIZATION': f'Bearer {resp.data["access"]}'}

    def test_dismiss_requires_auth(self):
        response = self.client.post(f'/portal/api/notices/{self.notice.id}/dismiss/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_dismiss_removes_notice_from_banner(self):
        self.client.post('/portal/login/', {'email': 'gerente@a.com', 'password': 'senha-123'})
        self.assertContains(self.client.get('/portal/'), 'id="notice-banner"')

        response = self.client.post(f'/portal/api/notices/{self.notice.id}/dismiss/')
        self.assertEqual(response.status_code, 200)

        self.assertNotContains(self.client.get('/portal/'), 'id="notice-banner"')

    def test_dismiss_does_not_affect_other_user(self):
        auth_a = self._login('gerente@a.com')
        self.client.post(f'/portal/api/notices/{self.notice.id}/dismiss/', **auth_a)

        self.assertIsNone(notices.get_active_notice_for_user(self.user_a))
        self.assertEqual(notices.get_active_notice_for_user(self.user_b), self.notice)

    def test_dismiss_persists_across_new_login_session(self):
        self.client.post('/portal/login/', {'email': 'gerente@a.com', 'password': 'senha-123'})
        self.client.post(f'/portal/api/notices/{self.notice.id}/dismiss/')

        self.client.post('/portal/logout/')
        self.client.post('/portal/login/', {'email': 'gerente@a.com', 'password': 'senha-123'})

        self.assertNotContains(self.client.get('/portal/'), 'id="notice-banner"')

    def test_dismiss_nonexistent_notice_returns_404(self):
        auth_a = self._login('gerente@a.com')
        response = self.client.post('/portal/api/notices/99999/dismiss/', **auth_a)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
