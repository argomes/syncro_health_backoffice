from django.urls import path

from . import views

urlpatterns = [
    path('presigned-url/', views.presigned_url, name='backup-presigned-url'),
    path('list/', views.list_backups, name='backup-list'),
]
