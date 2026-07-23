from django.urls import path

from .views import MunicipioSearchView

urlpatterns = [
    path('', MunicipioSearchView.as_view(), name='municipio_search'),
]
