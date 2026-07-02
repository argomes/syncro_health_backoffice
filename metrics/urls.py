from django.urls import path
from . import views

urlpatterns = [
    path('heartbeat', views.heartbeat, name='metrics-heartbeat'),
    path('logs', views.logs, name='metrics-logs'),
]
