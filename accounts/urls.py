from rest_framework.routers import DefaultRouter
from .views import ClinicAccessViewSet

router = DefaultRouter()
router.register('clinic-access', ClinicAccessViewSet, basename='clinic-access')

urlpatterns = router.urls
