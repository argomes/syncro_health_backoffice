from django.core.cache import cache
from django.db.models.signals import post_save
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from clinics.models import ClinicStatus
from .models import Ticket, sync_ticket_to_zoho
from .tests import make_clinic


class ErrorReportAPITest(TestCase):
    """
    EDGW-052 — POST /api/support/error-reports/, autenticado por
    X-License-Key (canal gateway -> backoffice), criando um Ticket local.
    """

    @classmethod
    def setUpClass(cls):
        # Mesmo motivo de TicketAPITest: evita chamar a API real do Zoho
        # Desk durante os testes (CELERY_TASK_ALWAYS_EAGER=True local).
        super().setUpClass()
        post_save.disconnect(sync_ticket_to_zoho, sender=Ticket)

    def setUp(self):
        # EDGW-052 (Security Engineer, 2026-07-27): ErrorReportRateThrottle
        # limita 15/hora por IP; sem limpar o cache entre testes, o client
        # de teste (mesmo IP) esgota a cota e recebe 429 nos testes
        # seguintes da mesma classe (mesmo padrão de clinics/tests_db_access_grant.py).
        cache.clear()
        self.client = APIClient()
        self.clinic = make_clinic()
        self.url = '/api/support/error-reports/'

    def _valid_payload(self, **overrides):
        payload = {
            'category': 'sincronizacao',
            'description': 'Falha ao sincronizar agenda do dia.',
            'severity': 'alto',
            'reporter_role': 'recepcao',
            'contact_name': 'Fulana',
            'contact_email': 'fulana@clinica.com',
        }
        payload.update(overrides)
        return payload

    def test_without_license_key_returns_401(self):
        response = self.client.post(self.url, self._valid_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_with_invalid_license_key_returns_401(self):
        response = self.client.post(
            self.url,
            self._valid_payload(),
            format='json',
            HTTP_X_LICENSE_KEY='00000000-0000-0000-0000-000000000000',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_license_key_of_suspended_clinic_returns_401(self):
        self.clinic.status = ClinicStatus.SUSPENDED
        self.clinic.save(update_fields=['status'])
        response = self.client.post(
            self.url,
            self._valid_payload(),
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_valid_license_key_creates_ticket(self):
        response = self.client.post(
            self.url,
            self._valid_payload(),
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Ticket.objects.count(), 1)

        ticket = Ticket.objects.get()
        self.assertEqual(ticket.clinic_id, self.clinic.id)
        self.assertIsNone(ticket.created_by)
        self.assertEqual(ticket.priority, 'high')
        self.assertIn('sincronizacao', ticket.title)
        self.assertIn('Falha ao sincronizar agenda do dia.', ticket.description)
        self.assertIn('Fulana', ticket.description)

        self.assertIn('ticket_id', response.data)
        self.assertEqual(response.data['ticket_id'], ticket.id)
        self.assertIn('zoho_ticket_id', response.data)
        self.assertEqual(response.data['zoho_ticket_id'], '')

    def test_another_clinic_license_key_scopes_ticket_to_itself(self):
        other_clinic = make_clinic(name='Outra Clínica')
        response = self.client.post(
            self.url,
            self._valid_payload(),
            format='json',
            HTTP_X_LICENSE_KEY=str(other_clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.clinic_id, other_clinic.id)

    def test_missing_required_fields_returns_400(self):
        for field in ('category', 'description', 'severity'):
            payload = self._valid_payload()
            del payload[field]
            response = self.client.post(
                self.url,
                payload,
                format='json',
                HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
            )
            self.assertEqual(
                response.status_code, status.HTTP_400_BAD_REQUEST,
                msg=f'campo {field} deveria ser obrigatório',
            )

    def test_invalid_severity_returns_400(self):
        response = self.client.post(
            self.url,
            self._valid_payload(severity='urgentissimo'),
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_optional_fields_can_be_omitted(self):
        payload = self._valid_payload()
        del payload['reporter_role']
        del payload['contact_name']
        del payload['contact_email']
        response = self.client.post(
            self.url,
            payload,
            format='json',
            HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_severity_values_map_to_all_priority_levels(self):
        cases = {
            'baixo': 'low',
            'medio': 'medium',
            'alto': 'high',
            'critico': 'critical',
        }
        for severity_input, expected_priority in cases.items():
            Ticket.objects.all().delete()
            response = self.client.post(
                self.url,
                self._valid_payload(severity=severity_input),
                format='json',
                HTTP_X_LICENSE_KEY=str(self.clinic.license_key),
            )
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertEqual(Ticket.objects.get().priority, expected_priority)


class ErrorReportEdgeCasesAPITest(TestCase):
    """
    EDGW-052 — casos de borda adicionais não cobertos em ErrorReportAPITest:
    strings em branco, variação de maiúsculas/acentuação em severity,
    description muito longa, criações em sequência rápida e isolamento
    cross-tenant explícito.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        post_save.disconnect(sync_ticket_to_zoho, sender=Ticket)

    def setUp(self):
        # EDGW-052 (Security Engineer, 2026-07-27): ErrorReportRateThrottle
        # limita 15/hora por IP; sem limpar o cache entre testes, o client
        # de teste (mesmo IP) esgota a cota e recebe 429 nos testes
        # seguintes da mesma classe (mesmo padrão de clinics/tests_db_access_grant.py).
        cache.clear()
        self.client = APIClient()
        self.clinic = make_clinic()
        self.url = '/api/support/error-reports/'

    def _valid_payload(self, **overrides):
        payload = {
            'category': 'sincronizacao',
            'description': 'Falha ao sincronizar agenda do dia.',
            'severity': 'alto',
            'reporter_role': 'recepcao',
            'contact_name': 'Fulana',
            'contact_email': 'fulana@clinica.com',
        }
        payload.update(overrides)
        return payload

    def _post(self, payload, clinic=None):
        return self.client.post(
            self.url,
            payload,
            format='json',
            HTTP_X_LICENSE_KEY=str((clinic or self.clinic).license_key),
        )

    # -- category/description vazios ou só espaços em branco --------------

    def test_blank_category_returns_400(self):
        response = self._post(self._valid_payload(category=''))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_whitespace_only_category_returns_400(self):
        response = self._post(self._valid_payload(category='   '))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_blank_description_returns_400(self):
        response = self._post(self._valid_payload(description=''))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_whitespace_only_description_returns_400(self):
        response = self._post(self._valid_payload(description='   \n\t  '))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_whitespace_only_severity_returns_400(self):
        # severity não tem validate_ que faz strip-then-empty-check
        # explícito como category/description; string em branco não bate
        # com nenhuma chave do SEVERITY_MAP, então deve cair no ValidationError
        # genérico de "severity inválida" (400), não em erro 500.
        response = self._post(self._valid_payload(severity='   '))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Ticket.objects.count(), 0)

    # -- severity com variação de maiúsculas/acentuação --------------------

    def test_severity_uppercase_accented(self):
        response = self._post(self._valid_payload(severity='CRÍTICO'))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Ticket.objects.get().priority, 'critical')

    def test_severity_mixed_case_no_accent(self):
        Ticket.objects.all().delete()
        response = self._post(self._valid_payload(severity='Critico'))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Ticket.objects.get().priority, 'critical')

    def test_severity_lowercase_accented_with_trailing_space(self):
        Ticket.objects.all().delete()
        response = self._post(self._valid_payload(severity='crítico '))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Ticket.objects.get().priority, 'critical')

    def test_severity_accented_medio_variant(self):
        Ticket.objects.all().delete()
        response = self._post(self._valid_payload(severity='MÉDIA'))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Ticket.objects.get().priority, 'medium')

    # -- description extremamente longa ------------------------------------

    def test_description_over_max_length_is_rejected(self):
        # EDGW-052 (Security Engineer, 2026-07-27): description agora tem
        # max_length=8000 no serializer para evitar payloads arbitrariamente
        # grandes esgotando fila do Celery/API do Zoho.
        response = self._post(self._valid_payload(description='A' * 8001))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_description_at_max_length_is_persisted_in_full(self):
        long_description = 'A' * 8000
        response = self._post(self._valid_payload(description=long_description))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        ticket = Ticket.objects.get()
        self.assertIn(long_description, ticket.description)

    def test_long_category_truncates_title_safely(self):
        # category tem max_length=255 no serializer; mesmo no limite, o
        # title = f'[Erro Desktop] {category}'[:255] é truncado sem estourar
        # o max_length=255 do model Ticket.title.
        long_category = 'X' * 255
        response = self._post(self._valid_payload(category=long_category))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        ticket = Ticket.objects.get()
        self.assertLessEqual(len(ticket.title), 255)

    def test_category_over_max_length_returns_400(self):
        response = self._post(self._valid_payload(category='X' * 256))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Ticket.objects.count(), 0)

    # -- múltiplos error-reports da mesma clínica em sequência rápida ------

    def test_multiple_rapid_reports_same_clinic_create_distinct_tickets(self):
        responses = [self._post(self._valid_payload(category=f'erro-{i}')) for i in range(10)]

        for response in responses:
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        tickets = Ticket.objects.filter(clinic=self.clinic)
        self.assertEqual(tickets.count(), 10)

        # Sem duplicação: 10 IDs distintos, todos vinculados à clínica certa.
        ticket_ids = set(tickets.values_list('id', flat=True))
        self.assertEqual(len(ticket_ids), 10)

        response_ticket_ids = {r.data['ticket_id'] for r in responses}
        self.assertEqual(response_ticket_ids, ticket_ids)

    # -- isolamento cross-tenant explícito ----------------------------------

    def test_license_key_cannot_create_ticket_visible_to_other_clinic(self):
        other_clinic = make_clinic(name='Outra Clínica')

        response_a = self._post(self._valid_payload(category='clinica-a'), clinic=self.clinic)
        response_b = self._post(self._valid_payload(category='clinica-b'), clinic=other_clinic)

        self.assertEqual(response_a.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response_b.status_code, status.HTTP_201_CREATED)

        ticket_a = Ticket.objects.get(id=response_a.data['ticket_id'])
        ticket_b = Ticket.objects.get(id=response_b.data['ticket_id'])

        self.assertEqual(ticket_a.clinic_id, self.clinic.id)
        self.assertEqual(ticket_b.clinic_id, other_clinic.id)
        self.assertNotEqual(ticket_a.clinic_id, ticket_b.clinic_id)

        # Clínica A não enxerga o ticket criado pela clínica B via seu
        # próprio license_key (mesma regra usada em TicketViewSet.get_queryset).
        self.assertEqual(
            Ticket.objects.filter(clinic=self.clinic).count(), 1,
        )
        self.assertEqual(
            Ticket.objects.filter(clinic=other_clinic).count(), 1,
        )


class ClinicSupportTicketRestrictedFlagTest(TestCase):
    """
    EDGW-052 (pré-requisito de BACFF-AVULSA-09) — só o campo persistido +
    migração aplicada + exposição em get_license_info; UI/RBAC ficam para
    outra task.
    """

    def test_default_is_false(self):
        clinic = make_clinic()
        self.assertFalse(clinic.support_ticket_restricted_to_admin)

    def test_field_persists_true(self):
        clinic = make_clinic()
        clinic.support_ticket_restricted_to_admin = True
        clinic.save(update_fields=['support_ticket_restricted_to_admin'])
        clinic.refresh_from_db()
        self.assertTrue(clinic.support_ticket_restricted_to_admin)
