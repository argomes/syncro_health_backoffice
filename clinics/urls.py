from rest_framework.routers import DefaultRouter
from django.urls import path
from .views import ClinicViewSet
from .views import validate_license_production

router = DefaultRouter()
router.register('', ClinicViewSet, basename='clinic')
urlpatterns = [
    path('license/validate-prod/', validate_license_production, name='validate_license_production'),
] + router.urls
