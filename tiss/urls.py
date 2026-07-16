from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    TISSOperatorConfigViewSet, TISSLoteViewSet, TISSGuiaViewSet, estatisticas,
    verificar_elegibilidade, registrar_elegibilidade_manual_view,
)

router = DefaultRouter()
router.register('operadoras', TISSOperatorConfigViewSet, basename='tiss-operadoras')
router.register('lotes', TISSLoteViewSet, basename='tiss-lotes')
router.register('guias', TISSGuiaViewSet, basename='tiss-guias')

urlpatterns = [
    path('estatisticas/', estatisticas, name='tiss_estatisticas'),
    # BACFF-013: consumidas pelo Edge Gateway (license_key), não pela UI do backoffice.
    path('elegibilidade/verificar/', verificar_elegibilidade, name='tiss_elegibilidade_verificar'),
    path('elegibilidade/manual/', registrar_elegibilidade_manual_view, name='tiss_elegibilidade_manual'),
    path('', include(router.urls)),
]
