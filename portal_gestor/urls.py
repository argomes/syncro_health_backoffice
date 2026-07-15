from django.urls import path

from . import views

urlpatterns = [
    path('reports/sessions/', views.ReportSessionCreateView.as_view(), name='report_session_create'),
    path('reports/sessions/<uuid:session_id>/', views.ReportSessionDetailView.as_view(), name='report_session_detail'),
    path('reports/sessions/<uuid:session_id>/patients/', views.PatientsReportView.as_view(), name='report_session_patients'),
    path('reports/sessions/<uuid:session_id>/appointments/', views.AppointmentsReportView.as_view(), name='report_session_appointments'),
    path('dashboard/summary/', views.DashboardSummaryView.as_view(), name='dashboard_summary'),
    path('notices/<int:notice_id>/dismiss/', views.NoticeDismissView.as_view(), name='portal_notice_dismiss'),
]
