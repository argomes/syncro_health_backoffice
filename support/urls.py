from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import TicketViewSet, TicketMessageViewSet, create_error_report, list_my_error_reports
from .webhook_views import zoho_ticket_comment_webhook

router = DefaultRouter()
router.register('tickets', TicketViewSet, basename='ticket')
router.register(r'tickets/(?P<ticket_id>\d+)/messages', TicketMessageViewSet, basename='ticket-message')

urlpatterns = [
    # BACFF-AVULSA-10 — webhook de VOLTA (Zoho Desk -> Backoffice), ver
    # support/webhook_views.py para autenticação e formato do payload.
    path('webhooks/zoho-comment/', zoho_ticket_comment_webhook, name='zoho_ticket_comment_webhook'),
    # EDGW-052 — gateway repassa POST /error-reports do app desktop pra cá,
    # autenticado por X-License-Key.
    path('error-reports/', create_error_report, name='create_error_report'),
    # EDGW-067 — "Meus Chamados": gateway repassa GET com ?reporter_user_id=
    # filtrando pelo identificador estável de quem abriu o report.
    path('error-reports/mine/', list_my_error_reports, name='list_my_error_reports'),
] + router.urls
