from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.http import JsonResponse
from django.urls import path, include, reverse_lazy


def health_check(request):
    """
    Liveness check pro Dockerfile/Railway/qualquer orquestrador — de propósito
    não toca banco/redis (health check não deve falhar por uma dependência
    lenta/instável quando o processo em si está de pé; isso é o que
    distingue liveness de readiness). Sem auth, sem custo.
    """
    return JsonResponse({'status': 'ok'})
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from clinics.views import issue_service_token, get_license_info
from billing.webhook_views import asaas_webhook
from syncro_backoffice.throttling import LoginRateThrottle
from accounts.portal_views import ClinicTokenObtainPairView, ClinicTokenRefreshView, ClinicUserMeView
from accounts.views import logout as support_logout
from accounts.password_reset_clinic import ClinicPasswordResetConfirmView, ClinicPasswordResetView
from portal_gestor.template_views import (
    DashboardFragmentView,
    DashboardHomeView,
    PortalLoginView,
    PortalLogoutView,
    PortalRefreshView,
    ReportNewView,
    ReportResultsView,
    ReportStatusFragmentView,
    ReportStatusView,
)

class ThrottledTokenObtainPairView(TokenObtainPairView):
    throttle_classes = [LoginRateThrottle]

urlpatterns = [
    path('health/', health_check, name='health_check'),
    path('admin/', admin.site.urls),
    path('api/auth/login/', ThrottledTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/auth/logout/', support_logout, name='support_logout'),

    # API v1 contracts
    path('api/v1/auth/service-token/', issue_service_token, name='issue_service_token'),
    path('api/v1/license/', get_license_info, name='get_license_info'),
    path('api/v1/billing/webhook/asaas/', asaas_webhook, name='asaas_webhook'),

    path('api/clinics/', include('clinics.urls')),
    path('api/billing/', include('billing.urls')),
    path('api/metrics/', include('metrics.urls')),
    path('api/accounts/', include('accounts.urls')),
    path('api/support/', include('support.urls')),
    path('api/tiss/', include('tiss.urls')),

    # TASK-BO-12 — "esqueci minha senha" pra SupportUser (equipe interna,
    # AUTH_USER_MODEL). Views built-in do Django puras — SupportUser é o
    # único AUTH_USER_MODEL do projeto, então get_user_model() já resolve
    # certo sem nenhuma view custom.
    #
    # template_name/email_template_name/subject_template_name explícitos e
    # com prefixo support_ (em vez dos nomes default do Django, tipo
    # registration/password_reset_form.html): django.contrib.admin já
    # empacota templates com esses nomes exatos em
    # django/contrib/admin/templates/registration/ pro próprio fluxo de
    # reset do admin, e como 'django.contrib.admin' vem antes de 'accounts'
    # em INSTALLED_APPS, o loader de templates (APP_DIRS) acharia a versão
    # do admin primeiro e ignoraria silenciosamente a nossa — nomes
    # explícitos e únicos evitam essa colisão em vez de depender da ordem
    # de INSTALLED_APPS pra desempatar.
    path(
        'admin-password-reset/',
        auth_views.PasswordResetView.as_view(
            template_name='registration/support_password_reset_form.html',
            email_template_name='registration/support_password_reset_email.txt',
            html_email_template_name='registration/support_password_reset_email.html',
            subject_template_name='registration/support_password_reset_subject.txt',
            success_url=reverse_lazy('admin_password_reset_done'),
        ),
        name='admin_password_reset',
    ),
    path(
        'admin-password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='registration/support_password_reset_done.html',
        ),
        name='admin_password_reset_done',
    ),
    path(
        'admin-password-reset/confirm/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='registration/support_password_reset_confirm.html',
            success_url=reverse_lazy('admin_password_reset_complete'),
        ),
        name='admin_password_reset_confirm',
    ),
    path(
        'admin-password-reset/complete/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='registration/support_password_reset_complete.html',
        ),
        name='admin_password_reset_complete',
    ),

    # portal_gestor — login de ClinicUser (admin/gerente da clínica), auth separada
    # de SupportUser. TASK-042a — base para TASK-042/043 (emissão/leitura de relatórios).
    path('portal/api/auth/login/', ClinicTokenObtainPairView.as_view(), name='clinic_token_obtain_pair'),
    path('portal/api/auth/refresh/', ClinicTokenRefreshView.as_view(), name='clinic_token_refresh'),
    path('portal/api/auth/me/', ClinicUserMeView.as_view(), name='clinic_user_me'),
    path('portal/api/', include('portal_gestor.urls')),

    # portal_gestor — páginas server-rendered (Django Templates + HTMX, TASK-046+).
    # JWT em cookie httpOnly, transporte separado das rotas JSON acima.
    path('portal/login/', PortalLoginView.as_view(), name='portal_login'),
    path('portal/logout/', PortalLogoutView.as_view(), name='portal_logout'),
    path('portal/refresh/', PortalRefreshView.as_view(), name='portal_refresh'),
    path('portal/dashboard/fragment/', DashboardFragmentView.as_view(), name='portal_dashboard_fragment'),
    path('portal/relatorios/novo/', ReportNewView.as_view(), name='portal_report_new'),
    path('portal/relatorios/<uuid:session_id>/', ReportStatusView.as_view(), name='portal_report_status'),
    path('portal/relatorios/<uuid:session_id>/status/fragment/', ReportStatusFragmentView.as_view(), name='portal_report_status_fragment'),
    path('portal/relatorios/<uuid:session_id>/resultados/', ReportResultsView.as_view(), name='portal_report_results'),

    # TASK-BO-12 — "esqueci minha senha" pra ClinicUser (gestor da clínica no
    # Portal Gestor). ClinicUser não é AUTH_USER_MODEL — ver
    # accounts/password_reset_clinic.py pra detalhe de por que precisa de
    # views próprias em vez das puras django.contrib.auth.views.
    path(
        'portal/password-reset/',
        ClinicPasswordResetView.as_view(),
        name='portal_password_reset',
    ),
    path(
        'portal/password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(template_name='portal_gestor/password_reset_done.html'),
        name='portal_password_reset_done',
    ),
    path(
        'portal/password-reset/confirm/<uidb64>/<token>/',
        ClinicPasswordResetConfirmView.as_view(),
        name='portal_password_reset_confirm',
    ),
    path(
        'portal/password-reset/complete/',
        auth_views.PasswordResetCompleteView.as_view(template_name='portal_gestor/password_reset_complete.html'),
        name='portal_password_reset_complete',
    ),

    path('portal/', DashboardHomeView.as_view(), name='portal_home'),
]
