from django.urls import path

from .views import CepSearchView

urlpatterns = [
    path('', CepSearchView.as_view(), name='cep_search'),
]
