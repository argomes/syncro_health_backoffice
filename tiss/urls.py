from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import TISSOperatorConfigViewSet, TISSLoteViewSet, TISSGuiaViewSet, estatisticas

router = DefaultRouter()
router.register('operadoras', TISSOperatorConfigViewSet, basename='tiss-operadoras')
router.register('lotes', TISSLoteViewSet, basename='tiss-lotes')
router.register('guias', TISSGuiaViewSet, basename='tiss-guias')

urlpatterns = [
    path('estatisticas/', estatisticas, name='tiss_estatisticas'),
    path('', include(router.urls)),
]
