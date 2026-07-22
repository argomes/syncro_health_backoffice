from django.urls import path

from .views import ListarFeriadosClinicaView

urlpatterns = [
    path('', ListarFeriadosClinicaView.as_view(), name='listar_feriados_clinica'),
]
