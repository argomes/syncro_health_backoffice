from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    TISSOperatorConfigViewSet, TISSLoteViewSet, TISSGuiaViewSet, estatisticas,
    verificar_elegibilidade, registrar_elegibilidade_manual_view,
    procedure_code_lookup, insurance_operator_lookup,
    procedure_code_search, insurance_operator_search,
    sync_documentos_pendentes, sync_documentos_assinatura,
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
    # TASK-BO-10: consumidas pelo SyncWorker do gateway (X-License-Key), pull/push
    # de fragmentos/assinatura XMLDSig de envioDocumentoWS (Orizon).
    path('xmldsig/sync/pendentes/', sync_documentos_pendentes, name='tiss_xmldsig_sync_pendentes'),
    path('xmldsig/sync/assinatura/', sync_documentos_assinatura, name='tiss_xmldsig_sync_assinatura'),
    # EDGW-013: fonte de verdade das tabelas de referência (cache-aside no gateway).
    # As rotas /search/ precisam vir ANTES das rotas <str:...>/ (senão "search"
    # seria capturado como um tuss_code/ans_code literal).
    path('reference/procedure-codes/search/', procedure_code_search, name='tiss_procedure_code_search'),
    path('reference/operators/search/', insurance_operator_search, name='tiss_insurance_operator_search'),
    path('reference/procedure-codes/<str:tuss_code>/', procedure_code_lookup, name='tiss_procedure_code_lookup'),
    path('reference/operators/<str:ans_code>/', insurance_operator_lookup, name='tiss_insurance_operator_lookup'),
    path('', include(router.urls)),
]
