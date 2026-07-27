from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import TicketViewSet, TicketMessageViewSet
from .webhook_views import zoho_ticket_comment_webhook

router = DefaultRouter()
router.register('tickets', TicketViewSet, basename='ticket')
router.register(r'tickets/(?P<ticket_id>\d+)/messages', TicketMessageViewSet, basename='ticket-message')

urlpatterns = [
    # BACFF-AVULSA-10 — webhook de VOLTA (Zoho Desk -> Backoffice), ver
    # support/webhook_views.py para autenticação e formato do payload.
    path('webhooks/zoho-comment/', zoho_ticket_comment_webhook, name='zoho_ticket_comment_webhook'),
] + router.urls
