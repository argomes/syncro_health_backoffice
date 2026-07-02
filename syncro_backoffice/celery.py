import os
from celery import Celery

# Define as configurações padrão do Django para o Celery
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'syncro_backoffice.settings')

app = Celery('syncro_backoffice')

# Lê as configurações do Django. Todas as chaves do Celery devem começar com CELERY_
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-descobre tasks em todas as apps registradas
app.autodiscover_tasks()
