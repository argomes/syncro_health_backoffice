"""
TASK-046 — login/logout/refresh das páginas server-rendered do portal_gestor,
gravando o JWT do ClinicUser em cookies httpOnly (em vez de retornar no body,
como as views JSON de accounts/portal_views.py fazem para consumidores API).

Reaproveita os mesmos serializers da TASK-042a (ClinicTokenObtainPairSerializer/
ClinicTokenRefreshSerializer) — só muda o transporte do token, não a lógica de
autenticação/emissão.
"""
from datetime import datetime, time

from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.csrf import csrf_protect
from rest_framework.exceptions import PermissionDenied, ValidationError

from accounts.authentication import PORTAL_ACCESS_COOKIE
from accounts.portal_serializers import ClinicTokenObtainPairSerializer, ClinicTokenRefreshSerializer
from support.models import Ticket

from . import report_reads, services
from .dashboard import HEARTBEAT_STALE_THRESHOLD_MINUTES, get_dashboard_summary
from .models import ReportSession

# Acima deste limite de atraso (minutos desde o último heartbeat), o card de
# sincronização mostra "desconectado" (vermelho) em vez de "atrasado"
# (amarelo). Puramente de apresentação — não altera a semântica de
# `connected` já revisada em dashboard.py (TASK-048), só refina como um
# `connected=False` é exibido visualmente.
#
# Derivado como múltiplo do HEARTBEAT_STALE_THRESHOLD_MINUTES (TASK-048) em
# vez de um número solto: `connected=False` já significa "mais velho que
# HEARTBEAT_STALE_THRESHOLD_MINUTES" (hoje 10 min, 2x o intervalo esperado de
# ~5 min do HealthWorker); o card fica "amarelo" por mais um múltiplo desse
# mesmo intervalo antes de virar "vermelho", em vez de um segundo valor
# independente. Se HEARTBEAT_EXPECTED_INTERVAL_MINUTES mudar, este threshold
# acompanha automaticamente.
SYNC_OFFLINE_THRESHOLD_MINUTES = HEARTBEAT_STALE_THRESHOLD_MINUTES * 3

PORTAL_REFRESH_COOKIE = 'portal_refresh_token'
_REFRESH_COOKIE_PATH = '/portal/refresh/'
_DEFAULT_NEXT = '/portal/'


def _safe_next(request, raw_next):
    """
    Valida `next` contra open redirect: sem isso, um atacante pode montar um
    link tipo /portal/login/?next=https://evil.example/phish e, após um login
    legítimo, o usuário é redirecionado para fora do domínio confiável — clássico
    vetor de phishing pós-login. url_has_allowed_host_and_scheme só aceita path
    relativo ou host dentro de ALLOWED_HOSTS/request.get_host(), com scheme
    http(s) (nunca javascript:, data:, etc.).
    """
    if raw_next and url_has_allowed_host_and_scheme(
        url=raw_next, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return raw_next
    return _DEFAULT_NEXT


def _cookie_kwargs(max_age_timedelta):
    return dict(
        httponly=True,
        secure=settings.PORTAL_COOKIE_SECURE,
        samesite='Lax',
        max_age=int(max_age_timedelta.total_seconds()),
    )


def _set_auth_cookies(response, access, refresh=None):
    response.set_cookie(PORTAL_ACCESS_COOKIE, access, **_cookie_kwargs(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME']))
    if refresh is not None:
        response.set_cookie(
            PORTAL_REFRESH_COOKIE, refresh, path=_REFRESH_COOKIE_PATH,
            **_cookie_kwargs(settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME']),
        )
    return response


def _clear_auth_cookies(response):
    response.delete_cookie(PORTAL_ACCESS_COOKIE)
    response.delete_cookie(PORTAL_REFRESH_COOKIE, path=_REFRESH_COOKIE_PATH)
    return response


@method_decorator(csrf_protect, name='dispatch')
class PortalLoginView(View):
    template_name = 'portal_gestor/login.html'

    def get(self, request):
        next_url = _safe_next(request, request.GET.get('next'))
        return render(request, self.template_name, {'next': next_url})

    def post(self, request):
        next_url = _safe_next(request, request.POST.get('next'))
        serializer = ClinicTokenObtainPairSerializer(data={
            'email': request.POST.get('email', ''),
            'password': request.POST.get('password', ''),
        })
        try:
            serializer.is_valid(raise_exception=True)
        except ValidationError:
            # Mensagem genérica — mesma cautela já aplicada na API (não distinguir
            # "email não existe" de "senha errada", nem vazar motivo de LDAP/clínica
            # inativa em detalhe que ajude enumeração).
            return render(request, self.template_name, {
                'next': next_url,
                'error': 'E-mail ou senha inválidos.',
            }, status=400)

        data = serializer.validated_data
        response = redirect(next_url)
        return _set_auth_cookies(response, data['access'], data['refresh'])


class PortalLogoutView(View):
    def post(self, request):
        response = redirect(reverse('portal_login'))
        return _clear_auth_cookies(response)


@method_decorator(csrf_protect, name='dispatch')
class PortalRefreshView(View):
    """
    POST /portal/refresh/ — chamado via HTMX/JS quando uma página server-rendered
    recebe 401 do backend (access token expirado). Não é uma rota navegável
    diretamente pelo usuário.
    """

    def post(self, request):
        refresh = request.COOKIES.get(PORTAL_REFRESH_COOKIE)
        if not refresh:
            return _clear_auth_cookies(redirect(reverse('portal_login')))

        serializer = ClinicTokenRefreshSerializer(data={'refresh': refresh})
        try:
            serializer.is_valid(raise_exception=True)
        except ValidationError:
            return _clear_auth_cookies(redirect(reverse('portal_login')))

        data = serializer.validated_data
        next_url = _safe_next(request, request.POST.get('next'))
        response = redirect(next_url)
        return _set_auth_cookies(response, data['access'], data.get('refresh'))


def _sync_state(gateway_status: dict) -> str:
    """
    Deriva o estado visual do card de sincronização a partir do payload de
    dashboard.py::get_dashboard_summary. Mantido aqui (não em dashboard.py)
    porque é uma decisão de apresentação (4 cores) sobre um contrato de dados
    já revisado (connected: True/False/None) — não altera esse contrato.
    """
    connected = gateway_status['connected']
    if connected is None:
        return 'never'  # cinza — nunca sincronizou
    if connected:
        return 'ok'  # verde — sincronizado

    last_seen = gateway_status['last_seen']
    if last_seen is None:
        return 'offline'
    age_minutes = (timezone.now() - parse_datetime(last_seen)).total_seconds() / 60
    return 'delayed' if age_minutes <= SYNC_OFFLINE_THRESHOLD_MINUTES else 'offline'  # amarelo / vermelho


def _dashboard_context(request):
    summary = get_dashboard_summary(request.clinic)
    return {
        'summary': summary,
        'sync_state': _sync_state(summary['gateway_status']),
    }


class DashboardHomeView(View):
    """
    GET /portal/ — tela inicial pós-login (TASK-049). Renderiza o shell com os
    3 cards já preenchidos no primeiro load (evita um round-trip HTMX
    desnecessário logo na entrada); o próprio fragmento inclui o atributo
    hx-get que passa a se auto-atualizar a cada 30s a partir daí (padrão HTMX
    de "self-updating polling swap" — ver _dashboard_cards.html).

    Protegida pelo ClinicPortalAuthMiddleware (TASK-047), que já injeta
    request.clinic_user/request.clinic antes desta view rodar.
    """

    def get(self, request):
        return render(request, 'portal_gestor/dashboard.html', _dashboard_context(request))


class DashboardFragmentView(View):
    """
    GET /portal/dashboard/fragment/ — usada só pelo polling HTMX do card de
    sincronização (hx-trigger="every 30s"). Renderiza SÓ o fragmento dos 3
    cards (não a página inteira), reaproveitando a mesma
    get_dashboard_summary() do DashboardHomeView — sem round-trip pela API
    JSON (TASK-048), já que HTMX troca HTML, não JSON.
    """

    def get(self, request):
        return render(request, 'portal_gestor/_dashboard_cards.html', _dashboard_context(request))


@method_decorator(csrf_protect, name='dispatch')
class ReportNewView(View):
    """
    GET/POST /portal/relatorios/novo/ — formulário de criação de ReportSession
    (TASK-042, já implementada). Submit não bloqueia esperando sincronização —
    redireciona direto para a tela de status (ver nota "UX é obrigatoriamente
    assíncrona" na task doc TASK-042).

    csrf_protect explícito (mesmo com CsrfViewMiddleware global já ativo) para
    manter o mesmo padrão de PortalLoginView/PortalRefreshView — defesa em
    profundidade e documentação clara de que este POST manipula estado.
    """

    template_name = 'portal_gestor/report_new.html'

    def get(self, request):
        return render(request, self.template_name)

    def post(self, request):
        raw_date_from = request.POST.get('date_from', '')
        raw_date_to = request.POST.get('date_to', '')
        entities = request.POST.getlist('entities')
        form_data = {'date_from': raw_date_from, 'date_to': raw_date_to, 'entities': entities}

        date_from = self._parse_date(raw_date_from, time.min)
        date_to = self._parse_date(raw_date_to, time.max)

        error = self._validate(date_from, date_to, entities)
        if error:
            return render(request, self.template_name, {'error': error, 'form_data': form_data}, status=400)

        session = services.create_report_session(
            clinic=request.clinic,
            created_by=request.clinic_user,
            entities=entities,
            date_from=date_from,
            date_to=date_to,
        )
        return redirect('portal_report_status', session_id=session.session_id)

    @staticmethod
    def _parse_date(raw_value, time_component):
        try:
            naive = datetime.combine(datetime.strptime(raw_value, '%Y-%m-%d').date(), time_component)
        except ValueError:
            return None
        return timezone.make_aware(naive) if timezone.is_naive(naive) else naive

    @staticmethod
    def _validate(date_from, date_to, entities):
        if date_from is None or date_to is None:
            return 'Informe um período válido (de/até).'
        if date_from >= date_to:
            return 'A data inicial deve ser anterior à data final.'
        if not entities:
            return 'Selecione ao menos um tipo de dado (pacientes ou agendamentos).'
        return None


class ReportStatusView(View):
    """
    GET /portal/relatorios/{session_id}/ — página de status com polling HTMX
    (ver _report_status_fragment.html). Escopada à própria clínica — 404 se a
    sessão for de outra clínica ou não existir.
    """

    def get(self, request, session_id):
        session = _get_own_session_or_404(request, session_id)
        return render(request, 'portal_gestor/report_status.html', {'session': session})


class ReportStatusFragmentView(View):
    """GET /portal/relatorios/{session_id}/status/fragment/ — só o fragmento de status,
    usado pelo polling automático enquanto a sessão está `pending`."""

    def get(self, request, session_id):
        session = _get_own_session_or_404(request, session_id)
        return render(request, 'portal_gestor/_report_status_fragment.html', {'session': session})


class ReportResultsView(View):
    """
    GET /portal/relatorios/{session_id}/resultados/ — chama report_reads
    (TASK-043) diretamente; trata PermissionDenied (sessão expirada/não
    pronta/fora de escopo) e ReportUnavailable (Postgres da clínica
    inacessível) com mensagem amigável, nunca como erro genérico 500.
    """

    def get(self, request, session_id):
        session = _get_own_session_or_404(request, session_id)

        context = {'session': session}
        try:
            if 'patients' in session.entities_scope:
                context['patients'] = report_reads.read_patients_report(request.clinic, session)
            if 'appointments' in session.entities_scope:
                context['appointments'] = report_reads.read_appointments_report(request.clinic, session)
            if 'medical_records' in session.entities_scope:
                context['medical_records'] = report_reads.read_medical_records_report(request.clinic, session)
        except PermissionDenied:
            context = {
                'session': session,
                'error': 'Esta sessão expirou ou ainda não está pronta para leitura. Gere um novo relatório.',
                'error_action': 'new',
            }
        except report_reads.ReportUnavailable:
            context = {
                'session': session,
                'error': 'Não foi possível consultar os dados da clínica agora. Tente novamente em alguns minutos.',
            }

        return render(request, 'portal_gestor/report_results.html', context)


def _get_own_session_or_404(request, session_id):
    try:
        return ReportSession.objects.get(session_id=session_id, clinic=request.clinic)
    except ReportSession.DoesNotExist:
        raise Http404('report_session_not_found')


class TicketListView(View):
    """
    GET /portal/suporte/ — chamados de suporte abertos pela própria clínica
    (BACFF-AVULSA-09), incluindo os criados via app desktop (EDGW-052).
    Sempre `Ticket.objects.filter(clinic=request.clinic)` — nunca aceita
    filtro de clínica vindo do cliente.
    """

    def get(self, request):
        tickets = Ticket.objects.filter(clinic=request.clinic)
        return render(request, 'portal_gestor/tickets_list.html', {'tickets': tickets})


class TicketDetailView(View):
    """
    GET /portal/suporte/{id}/ — detalhe do chamado + mensagens sincronizadas
    (BACFF-AVULSA-10). Só consulta — responder pelo portal é fora de escopo
    desta task. 404 (não 403) para ticket de outra clínica.
    """

    def get(self, request, ticket_id):
        try:
            ticket = (
                Ticket.objects.filter(clinic=request.clinic)
                .prefetch_related('messages')
                .get(id=ticket_id)
            )
        except Ticket.DoesNotExist:
            raise Http404('ticket_not_found')
        return render(request, 'portal_gestor/ticket_detail.html', {'ticket': ticket})


@method_decorator(csrf_protect, name='dispatch')
class SupportSettingsView(View):
    """
    GET/POST /portal/suporte/configuracoes/ — liga/desliga
    `Clinic.support_ticket_restricted_to_admin` (EDGW-052/BACFF-AVULSA-09).
    A sincronização para o gateway já acontece via `get_license_info`
    (clinics/views.py) — esta view só persiste o campo.
    """

    template_name = 'portal_gestor/support_settings.html'

    def get(self, request):
        return render(request, self.template_name, {'clinic': request.clinic})

    def post(self, request):
        clinic = request.clinic
        clinic.support_ticket_restricted_to_admin = 'support_ticket_restricted_to_admin' in request.POST
        clinic.save(update_fields=['support_ticket_restricted_to_admin'])
        return render(request, self.template_name, {'clinic': clinic, 'saved': True})
