from django.urls import path

from . import views

urlpatterns = [
    path('reports/sessions/', views.ReportSessionCreateView.as_view(), name='report_session_create'),
    path('reports/sessions/<uuid:session_id>/', views.ReportSessionDetailView.as_view(), name='report_session_detail'),
    path('reports/sessions/<uuid:session_id>/patients/', views.PatientsReportView.as_view(), name='report_session_patients'),
    path('reports/sessions/<uuid:session_id>/appointments/', views.AppointmentsReportView.as_view(), name='report_session_appointments'),
    path('reports/sessions/<uuid:session_id>/professionals/', views.ProfessionalsReportView.as_view(), name='report_session_professionals'),
    path('dashboard/summary/', views.DashboardSummaryView.as_view(), name='dashboard_summary'),
    path('notices/<int:notice_id>/dismiss/', views.NoticeDismissView.as_view(), name='portal_notice_dismiss'),
    path('support/tickets/', views.TicketListView.as_view(), name='api_support_tickets'),
    path('support/tickets/<int:ticket_id>/', views.TicketDetailView.as_view(), name='api_support_ticket_detail'),
    path('support/settings/', views.SupportSettingsView.as_view(), name='api_support_settings'),
]
