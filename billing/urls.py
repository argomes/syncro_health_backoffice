from rest_framework.routers import DefaultRouter
from .views import PlanViewSet, InvoiceViewSet

router = DefaultRouter()
router.register('plans', PlanViewSet, basename='plan')
router.register('invoices', InvoiceViewSet, basename='invoice')

urlpatterns = router.urls
