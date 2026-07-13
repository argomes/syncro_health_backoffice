from django.contrib import admin
from django.urls import path, include
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from clinics.views import issue_service_token, get_license_info
from billing.webhook_views import asaas_webhook
from syncro_backoffice.throttling import LoginRateThrottle
from accounts.portal_views import ClinicTokenObtainPairView, ClinicTokenRefreshView, ClinicUserMeView
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
    path('admin/', admin.site.urls),
    path('api/auth/login/', ThrottledTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # API v1 contracts
    path('api/v1/auth/service-token/', issue_service_token, name='issue_service_token'),
    path('api/v1/license/', get_license_info, name='get_license_info'),
    path('api/v1/billing/webhook/asaas/', asaas_webhook, name='asaas_webhook'),

    path('api/clinics/', include('clinics.urls')),
    path('api/billing/', include('billing.urls')),
    path('api/metrics/', include('metrics.urls')),
    path('api/accounts/', include('accounts.urls')),
    path('api/support/', include('support.urls')),

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
    path('portal/', DashboardHomeView.as_view(), name='portal_home'),
]
