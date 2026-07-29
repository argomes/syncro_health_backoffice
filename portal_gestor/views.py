import uuid

from django.http import HttpResponse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import ClinicCookieJWTAuthentication, ClinicJWTAuthentication
from clinics.permissions import IsClinicUser
from support.models import Ticket

from . import notices, report_reads, services
from .dashboard import get_dashboard_summary
from .models import PortalReadAuditLog, ReportSession
from .serializers import (
    PortalTicketDetailSerializer,
    PortalTicketListSerializer,
    ReportSessionCreateSerializer,
    ReportSessionSerializer,
    SupportSettingsSerializer,
)


class ReportSessionCreateView(APIView):
    """
    POST /portal/api/reports/sessions/ — clique de "Gerar Relatório" no portal_gestor.

    Retorna IMEDIATAMENTE (session_id + status=pending) — não bloqueia esperando
    a sincronização do gateway, que só acontece no próximo heartbeat (~5 min) e
    pode levar ~2 ciclos até os dados estarem disponíveis (ver nota "UX é
    obrigatoriamente assíncrona" na task doc TASK-042). O frontend deve fazer
    polling em GET /portal/api/reports/sessions/{session_id}/ até status=ready.
    """

    authentication_classes = [ClinicJWTAuthentication]
    permission_classes = [IsClinicUser]

    def post(self, request):
        serializer = ReportSessionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        session = services.create_report_session(
            clinic=request.user.clinic,
            created_by=request.user,
            entities=data['entities'],
            date_from=data['date_from'],
            date_to=data['date_to'],
        )

        return Response(ReportSessionSerializer(session).data, status=status.HTTP_201_CREATED)


class ReportSessionDetailView(APIView):
    """
    GET /portal/api/reports/sessions/{session_id}/ — usado pelo frontend para
    fazer polling do status até a sessão ficar `ready` (ver nota TASK-042).
    Escopado à própria clínica do ClinicUser — nunca resolve sessão de outra.
    """

    authentication_classes = [ClinicJWTAuthentication]
    permission_classes = [IsClinicUser]

    def get(self, request, session_id):
        try:
            session = ReportSession.objects.get(session_id=session_id, clinic=request.user.clinic)
        except ReportSession.DoesNotExist:
            return Response({'error': 'not_found'}, status=status.HTTP_404_NOT_FOUND)

        return Response(ReportSessionSerializer(session).data)


class _ReportReadView(APIView):
    """
    Base para os endpoints de leitura de relatório (TASK-043). Resolve a sessão
    escopada à própria clínica do ClinicUser (404 se for de outra clínica ou não
    existir) e delega a leitura/decriptação a portal_gestor.report_reads, que
    levanta PermissionDenied (403) para qualquer sessão expirada, ainda não
    entregue, ou fora do escopo de entidades pedido na criação — nunca cai em
    fallback silencioso para dado cru.
    """

    authentication_classes = [ClinicJWTAuthentication]
    permission_classes = [IsClinicUser]
    read_fn_name = None  # definido nas subclasses — lookup dinâmico via getattr,
    # não uma referência direta à função (que "congelaria" a versão original do
    # módulo em tempo de import e ignoraria patches em testes).
    entity_name = None  # definido nas subclasses — usado só para o registro de
    # auditoria (BACFF-AVULSA-05), nunca para autorização (isso é feito por
    # report_reads via _require_entity_in_scope).

    def get(self, request, session_id):
        try:
            session = ReportSession.objects.get(session_id=session_id, clinic=request.user.clinic)
        except ReportSession.DoesNotExist:
            return Response({'error': 'not_found'}, status=status.HTTP_404_NOT_FOUND)

        read_fn = getattr(report_reads, self.read_fn_name)
        results = read_fn(request.user.clinic, session)

        # BACFF-AVULSA-05 (LGPD Art. 37) — toda leitura BEM-SUCEDIDA gera um
        # registro de auditoria, mesmo com 0 resultados (o acesso autorizado em
        # si é a operação de tratamento a registrar). Gravado só depois de
        # `read_fn` retornar sem levantar (PermissionDenied/ReportUnavailable
        # não geram registro — não houve leitura de fato). Só metadados da
        # operação: NUNCA nenhum campo de `results` é persistido aqui.
        PortalReadAuditLog.objects.create(
            clinic_user=request.user,
            clinic=request.user.clinic,
            session_id=session.session_id,
            entity=self.entity_name,
            record_count=len(results),
        )

        return Response({'results': results, 'count': len(results)})


class PatientsReportView(_ReportReadView):
    """GET /portal/api/reports/sessions/{session_id}/patients/"""
    read_fn_name = 'read_patients_report'
    entity_name = 'patients'


class AppointmentsReportView(_ReportReadView):
    """GET /portal/api/reports/sessions/{session_id}/appointments/"""
    read_fn_name = 'read_appointments_report'
    entity_name = 'appointments'


class ProfessionalsReportView(_ReportReadView):
    """
    GET /portal/api/reports/sessions/{session_id}/professionals/

    Mesmo padrão anti-IDOR das outras views (sessão resolvida por
    `ReportSession.objects.get(session_id=..., clinic=request.user.clinic)` em
    `_ReportReadView.get`, 404 — nunca 403 — para sessão de outra clínica, para
    não confirmar existência a quem não tem acesso). A entidade `professionals`
    já sincroniza para a nuvem via `ProfessionalSyncStrategy` (syncro_gateway);
    faltava só o endpoint de leitura no portal.
    """
    read_fn_name = 'read_professionals_report'
    entity_name = 'professionals'


class DashboardSummaryView(APIView):
    """
    GET /portal/api/dashboard/summary/ — payload consolidado para a tela
    inicial (TASK-049): status do gateway, alerta de licença, sessões de
    relatório recentes. Aceita tanto o cookie httpOnly (páginas HTML via HTMX,
    TASK-046/047) quanto o header Authorization (consumidores API/SPA), já que
    esta é a primeira view do portal_gestor pensada para os dois transportes.
    """

    authentication_classes = [ClinicCookieJWTAuthentication, ClinicJWTAuthentication]
    permission_classes = [IsClinicUser]

    def get(self, request):
        return Response(get_dashboard_summary(request.user.clinic))


class TicketListView(APIView):
    """
    GET /portal/api/support/tickets/ — chamados de suporte abertos pela
    própria clínica (BACFF-AVULSA-09), incluindo os criados via app desktop
    (EDGW-052). Sempre escopado a `request.user.clinic` — nunca aceita
    `clinic_id` vindo do cliente (mesmo padrão anti-IDOR de _ReportReadView).
    """

    authentication_classes = [ClinicCookieJWTAuthentication, ClinicJWTAuthentication]
    permission_classes = [IsClinicUser]

    def get(self, request):
        tickets = Ticket.objects.filter(clinic=request.user.clinic)
        return Response(PortalTicketListSerializer(tickets, many=True).data)


class TicketDetailView(APIView):
    """
    GET /portal/api/support/tickets/{id}/ — detalhe do chamado + mensagens
    sincronizadas (BACFF-AVULSA-10). Só consulta — responder pelo portal é
    fora de escopo desta task. 404 (não 403) para ticket de outra clínica,
    mesmo cuidado anti-enumeração das outras views deste módulo.

    BACFF-AVULSA-11 (LGPD Art. 37) — `description` do ticket pode conter PHI
    de terceiros citada incidentalmente pelo atendente, então todo acesso
    BEM-SUCEDIDO gera um registro em `PortalReadAuditLog`, mesmo padrão de
    `_ReportReadView` (BACFF-AVULSA-05). Como não há `ReportSession` aqui
    (não é um relatório), `session_id` é um UUID gerado na hora só para
    correlacionar esta leitura específica — o campo não é FK, existe só como
    metadado de auditoria.
    """

    authentication_classes = [ClinicCookieJWTAuthentication, ClinicJWTAuthentication]
    permission_classes = [IsClinicUser]

    def get(self, request, ticket_id):
        try:
            ticket = Ticket.objects.filter(clinic=request.user.clinic).prefetch_related('messages').get(id=ticket_id)
        except Ticket.DoesNotExist:
            return Response({'error': 'not_found'}, status=status.HTTP_404_NOT_FOUND)

        PortalReadAuditLog.objects.create(
            clinic_user=request.user,
            clinic=request.user.clinic,
            session_id=uuid.uuid4(),
            entity='support_ticket',
            record_count=1,
        )

        return Response(PortalTicketDetailSerializer(ticket).data)


class SupportSettingsView(APIView):
    """
    GET/PATCH /portal/api/support/settings/ — liga/desliga
    `Clinic.support_ticket_restricted_to_admin` (EDGW-052), único campo de
    configuração de suporte editável pelo admin da clínica nesta task. A
    sincronização para o gateway já acontece via `get_license_info`
    (clinics/views.py) — esta view só precisa persistir o campo.
    """

    authentication_classes = [ClinicCookieJWTAuthentication, ClinicJWTAuthentication]
    permission_classes = [IsClinicUser]

    def get(self, request):
        return Response(SupportSettingsSerializer(request.user.clinic).data)

    def patch(self, request):
        clinic = request.user.clinic
        serializer = SupportSettingsSerializer(clinic, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class NoticeDismissView(APIView):
    """
    POST /portal/api/notices/{id}/dismiss/ — dispensa o aviso contextual
    (TASK-051) para o ClinicUser autenticado. Idempotente: dispensar de novo
    não é erro. Aceita cookie (chamado via HTMX pelo botão no banner) ou
    header Bearer, mesmo padrão de DashboardSummaryView.
    """

    authentication_classes = [ClinicCookieJWTAuthentication, ClinicJWTAuthentication]
    permission_classes = [IsClinicUser]

    def post(self, request, notice_id):
        found = notices.dismiss_notice(request.user, notice_id)
        if not found:
            return Response({'error': 'not_found'}, status=status.HTTP_404_NOT_FOUND)
        # Corpo vazio com 200 (não 204): HTMX não faz swap em respostas 204 por
        # padrão — o hx-swap="outerHTML" do banner precisa de um corpo (vazio)
        # pra efetivamente remover o elemento do DOM.
        return HttpResponse(status=200)
