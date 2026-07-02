from rest_framework.routers import DefaultRouter
from .views import TicketViewSet, TicketMessageViewSet

router = DefaultRouter()
router.register('tickets', TicketViewSet, basename='ticket')
router.register(r'tickets/(?P<ticket_id>\d+)/messages', TicketMessageViewSet, basename='ticket-message')

urlpatterns = router.urls
