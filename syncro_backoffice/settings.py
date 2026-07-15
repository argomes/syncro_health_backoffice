from pathlib import Path
from datetime import timedelta
import environ
from django.urls import reverse_lazy

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
)

environ.Env.read_env(BASE_DIR / '.env')

SECRET_KEY = env('SECRET_KEY')
DEBUG = env('DEBUG')
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1'])
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])

# Railway (e qualquer VPS atrás de nginx/Caddy/Traefik) termina TLS na borda
# e repassa a requisição pro gunicorn em HTTP puro — sem isso, request.is_secure()
# sempre retorna False mesmo servindo HTTPS de verdade, o que quebra o cookie
# Secure do portal_gestor (PORTAL_COOKIE_SECURE acima) e validação de CSRF
# sobre HTTPS. Seguro em dev também: runserver não popula X-Forwarded-Proto,
# então isso não afeta http://localhost local.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Controla o atributo Secure dos cookies httpOnly do portal_gestor (TASK-046)
# de forma independente de DEBUG — DEBUG também liga CORS_ALLOW_ALL_ORIGINS e
# outras coisas, então usar DEBUG como proxy pra "estamos em HTTPS?" acopla
# duas decisões que podem divergir (ex.: staging com DEBUG=True atrás de TLS).
# Default seguro: só desliga Secure quando explicitamente DEBUG=True.
PORTAL_COOKIE_SECURE = env.bool('PORTAL_COOKIE_SECURE', default=not DEBUG)

INSTALLED_APPS = [
    "unfold", # 1. Tem que ser o primeiro
    "unfold.contrib.filters", # 2. Filtros bonitos
    "unfold.contrib.forms", # 3. Forms bonitos
    "unfold.contrib.inlines", # 4. Inlines bonitos
    "unfold.contrib.import_export",  # optional, if django-import-export package is used
    "unfold.contrib.guardian",  # optional, if django-guardian package is used
    "unfold.contrib.simple_history",  # optional, if django-simple-history package is used
    "unfold.contrib.location_field",  # optional, if django-location-field package is used
    "unfold.contrib.constance",  # optional, if django-constance package is used
    "unfold.contrib.hijack",  # optional, if django-hijack package is used
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # third-party
    'rest_framework',
    'corsheaders',
    # local
    'accounts',
    'clinics',
    'billing',
    'metrics',
    'support',
    'integrations',
    'portal_gestor',
    'tiss',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'portal_gestor.middleware.ClinicPortalAuthMiddleware',
]

ROOT_URLCONF = 'syncro_backoffice.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'portal_gestor.context_processors.active_notice',
            ],
        },
    },
]

WSGI_APPLICATION = 'syncro_backoffice.wsgi.application'

DATABASES = {
    'default': env.db('DATABASE_URL', default=f'sqlite:///{BASE_DIR}/db.sqlite3')
}

# URL do superuser PostgreSQL — usada APENAS pelo provisioning.py para criar databases
PROVISIONING_DATABASE_URL = env('PROVISIONING_DATABASE_URL', default='')

# Nome de um banco Postgres marcado como TEMPLATE (datistemplate=true), com o schema do
# gateway (patients/appointments/dual envelope) já aplicado — se configurado, cada banco
# de clínica nasce com o schema pronto via CREATE DATABASE ... TEMPLATE, em vez de vazio
# (TASK-053, homologação). Vazio = comportamento anterior, banco vazio.
CLINIC_DB_TEMPLATE = env('CLINIC_DB_TEMPLATE', default='')

AUTH_USER_MODEL = 'accounts.SupportUser'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── REST Framework ────────────────────────────────────────────────────────────
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'accounts.authentication.SafeJWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '60/minute',
        'user': '300/minute',
        'login': '10/minute',
        'service_token': '30/minute',
        'webhook': '120/minute',
        'license': '60/minute',
    },
}

# ── JWT ───────────────────────────────────────────────────────────────────────
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=8),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'AUTH_HEADER_TYPES': ('Bearer',),
}

# ── CORS ──────────────────────────────────────────────────────────────────────
CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=[])
CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
    'x-license-key',
]

#--UNFOLD --#

UNFOLD = {
    "SITE_TITLE": "Syncro Health",
    "SITE_HEADER": "Syncro",
    "SITE_URL": "/",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "SHOW_BACK_BUTTON": True,
    "ENVIRONMENT": "staging", # mostra "Staging" amarelo no topo
    "COLORS": {
        "primary": {
            "50": "#f0f9ff",
            "100": "#e0f2fe",
            "500": "#0ea5e9",
            "600": "#0284c7",
            "700": "#0369a1",
            "900": "#082f49",   
        },
    },
    # TASK-057: busca geral (Cmd+K) cobrindo os models que um analista/admin
    # mais procura no dia a dia — clínica por nome/slug, gateway por clínica,
    # ticket de suporte.
    "COMMAND": {
        "search_models": [
            "clinics.Clinic",
            "metrics.SystemHeartbeat",
            "support.Ticket",
        ],
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        # TASK-057: substitui a lista crua de apps/models pelo agrupamento
        # operacional definido no PO (Principal/Operações/Sistema). Cada item
        # usa `permission` checando os grupos da TASK-056 — um usuário sem a
        # permissão correspondente simplesmente não vê o item (Unfold já trata
        # isso, não precisa de lógica extra aqui).
        "navigation": [
            {
                "title": "Principal",
                "separator": True,
                "items": [
                    {
                        "title": "Dashboard",
                        "icon": "dashboard",
                        "link": reverse_lazy("admin:index"),
                        "permission": lambda request: request.user.is_staff,
                    },
                    {
                        "title": "Clínicas",
                        "icon": "business",
                        "link": reverse_lazy("admin:clinics_clinic_changelist"),
                        "permission": lambda request: request.user.has_perm("clinics.view_clinic"),
                    },
                ],
            },
            {
                "title": "Operações",
                "separator": True,
                "items": [
                    {
                        "title": "Gateways",
                        "icon": "dns",
                        "link": reverse_lazy("admin:metrics_systemheartbeat_changelist"),
                        "permission": lambda request: request.user.has_perm("metrics.view_systemheartbeat"),
                    },
                    {
                        "title": "Relatórios",
                        "icon": "description",
                        "link": reverse_lazy("admin:portal_gestor_reportsession_changelist"),
                        "permission": lambda request: request.user.has_perm("portal_gestor.view_reportsession"),
                    },
                    # "Assinaturas" (ASAAS) e "Backups" entram aqui quando essas
                    # integrações existirem — sem model/tela ainda, não vale
                    # colocar um link morto no menu.
                ],
            },
            {
                "title": "Sistema",
                "separator": True,
                "items": [
                    {
                        "title": "Logs",
                        "icon": "terminal",
                        "link": reverse_lazy("admin:metrics_systemlog_changelist"),
                        "permission": lambda request: request.user.has_perm("metrics.view_systemlog"),
                    },
                    {
                        "title": "Suporte",
                        "icon": "support_agent",
                        "link": reverse_lazy("admin:support_ticket_changelist"),
                        "permission": lambda request: request.user.has_perm("support.view_ticket"),
                    },
                    {
                        "title": "Usuários & Permissões",
                        "icon": "admin_panel_settings",
                        "link": reverse_lazy("admin:auth_group_changelist"),
                        "permission": lambda request: request.user.is_superuser,
                    },
                ],
            },
        ],
    },
    "TABS": [
        {
            "models": [
                "clinicas.clinica",
                "clinicas.usuario",
            ],
            "separator": True,
            "items": [  # Chave obrigatória que corrige o KeyError
                {
                    "title": "Visão Geral",
                    "link": "admin:clinics_clinica_changelist",
                },
            ],
        }
    ],
}

NOTION_API_KEY = env('NOTION_API_KEY', default='')
NOTION_DATABASE_ID = env('NOTION_DATABASE_ID', default='')

# ASAAS Webhook — token configurado no painel ASAAS (Configurações > Notificações)
ASAAS_WEBHOOK_TOKEN = env('ASAAS_WEBHOOK_TOKEN', default='')

# Cache — usado por portal_gestor (TASK-042) para guardar a TemporaryKey
# efêmera (TTL = expires_at da ReportSession). PRECISA ser um backend
# compartilhado entre workers gunicorn em produção: LocMemCache (default do
# Django sem CACHES configurado) é por-processo e faria a chave "sumir"
# sempre que o heartbeat caísse num worker diferente do que criou a sessão,
# disparando o fallback de "cache evicted" (marca a sessão expired) de forma
# espúria em qualquer deploy multi-worker.
#
# Espelha o padrão já usado para CELERY_TASK_ALWAYS_EAGER: default seguro
# para dev local (LocMemCache, sem exigir infra) e explícito para produção
# via env. CACHE_URL DEVE ser setado em qualquer ambiente com DEBUG=False e
# mais de um worker gunicorn — setar sem isso quebra silenciosamente a
# garantia de TTL da TemporaryKey (ver TASK-042, nota de revisão de
# segurança). Reaproveita o Redis do Celery broker, DB separado (1) por
# padrão para não misturar namespaces.
CACHE_URL = env('CACHE_URL', default='')
if CACHE_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': CACHE_URL,
        }
    }
elif not DEBUG:
    # Produção sem CACHE_URL explícito: falha alto e cedo em vez de operar
    # silenciosamente sobre um cache por-processo que quebra a garantia de
    # TTL da TemporaryKey em qualquer deploy multi-worker.
    raise RuntimeError(
        'CACHE_URL não configurado com DEBUG=False. portal_gestor depende de '
        'um cache compartilhado entre workers (Redis) para a TemporaryKey '
        '(TASK-042) — LocMemCache silenciosamente quebra em produção '
        'multi-worker. Configure CACHE_URL (ex.: redis://host:6379/1).'
    )
# DEBUG=True sem CACHE_URL: usa o LocMemCache default do Django — adequado
# para dev local single-process (runserver), sem exigir Redis rodando.

# Celery Configurations
CELERY_BROKER_URL = env('CELERY_BROKER_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = env('CELERY_RESULT_BACKEND', default='redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
# Em desenvolvimento, executa tasks de forma síncrona (sem broker Redis).
# Sobrescreva com CELERY_TASK_ALWAYS_EAGER=False no .env de produção.
CELERY_TASK_ALWAYS_EAGER = env.bool('CELERY_TASK_ALWAYS_EAGER', default=DEBUG)

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# ADICIONE ESTA LINHA:
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# TASK-BO-08 — módulo TISS/SOAP (Orizon e outras operadoras)
#
# Chave Fernet para cifrar login/senha de operadora em repouso
# (tiss/crypto.py). Gerar com `Fernet.generate_key()`. Em dev sem valor
# configurado, cai num placeholder fixo (só serve pra rodar localmente sem
# exigir setup extra). Mesmo padrão de fail-fast já usado para CACHE_URL
# (ver acima): DEBUG=False sem TISS_FERNET_KEY explícito quebra o boot em
# vez de cifrar credenciais de operadora (TISSOperatorConfig) com uma chave
# placeholder pública e conhecida — o que equivaleria a não cifrar nada.
TISS_FERNET_KEY = env(
    'TISS_FERNET_KEY',
    default='Uu6l1z9ZQvX3m6nF8pQe2sYt7wA1bC4dE5fG6hJ8kL0=' if DEBUG else None,
)
if not DEBUG and not TISS_FERNET_KEY:
    raise RuntimeError(
        'TISS_FERNET_KEY não configurado com DEBUG=False. As credenciais de '
        'operadora TISS (TISSOperatorConfig) são cifradas em repouso com '
        'essa chave (tiss/crypto.py) — usar o placeholder de dev em '
        'produção equivale a não cifrar nada, pois a chave é pública neste '
        'repositório. Configure TISS_FERNET_KEY (gerar com '
        '`Fernet.generate_key()`).'
    )

# Diretório com os XSDs oficiais ANS (tissV4_02_00.xsd e includes) usados
# por tiss/xml_validator.py para validar o lote antes do envio SOAP. Fonte
# de verdade fora deste repositório — apontar para onde a documentação
# oficial ANS (PadroTISSComunicao) foi baixada no ambiente.
TISS_XSD_DIR = env(
    'TISS_XSD_DIR',
    default=str(
        BASE_DIR.parent
        / 'Documents'
        / 'PadroTISSComunicao202505'
        / 'Padrão TISS Comunicação 040200'
    ),
)

# Quando True, tiss/soap_client.py intercepta a chamada SOAP e devolve uma
# resposta fixa (sucesso ou erro) em vez de bater na rede — permite testar
# o fluxo completo de envio de lote sem credenciais/sandbox de operadora real.
TISS_SOAP_MOCK = env.bool('TISS_SOAP_MOCK', default=DEBUG)

# TASK-BO-12 — Email transacional via ZeptoMail (Zoho SMTP relay), usado hoje
# só pelo fluxo de "esqueci minha senha" (SupportUser e ClinicUser — ver
# accounts/password_reset_views.py). django.core.mail (SMTP built-in do
# Django) é suficiente para esse volume; django-anymail não se justifica
# aqui (só compensaria se precisássemos de tracking de bounce/webhook).
#
# Mesmo padrão de fail-fast já usado para CACHE_URL e TISS_FERNET_KEY (ver
# acima): em dev (DEBUG=True) cai em defaults de sandbox que não mandam
# email de verdade sem credencial; em produção (DEBUG=False), qualquer
# EMAIL_HOST_USER/EMAIL_HOST_PASSWORD ausente quebra o boot — a alternativa
# seria o Django tentar autenticar no SMTP sem credencial e falhar em
# runtime no meio de um fluxo de reset de senha do usuário, silenciosamente
# do ponto de vista de quem sobe o processo.
EMAIL_BACKEND = env(
    'EMAIL_BACKEND',
    default='django.core.mail.backends.console.EmailBackend' if DEBUG else 'django.core.mail.backends.smtp.EmailBackend',
)
EMAIL_HOST = env('EMAIL_HOST', default='smtp.zeptomail.com' if DEBUG else '')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='naoresponda@syncrohealth.com.br' if DEBUG else '')

if not DEBUG and (not EMAIL_HOST_USER or not EMAIL_HOST_PASSWORD or not EMAIL_HOST or not DEFAULT_FROM_EMAIL):
    raise RuntimeError(
        'Configuração de email incompleta com DEBUG=False. O fluxo de reset '
        'de senha (SupportUser e ClinicUser, TASK-BO-12) depende de '
        'EMAIL_HOST, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD e '
        'DEFAULT_FROM_EMAIL configurados via env — sem isso, o Django '
        'tentaria autenticar no SMTP sem credencial e o reset de senha '
        'falharia silenciosamente em produção. Configure as variáveis do '
        'ZeptoMail (SMTP relay do Zoho, token gerado no painel Mail Agent).'
    )
# DEBUG=True sem EMAIL_HOST_USER/PASSWORD: usa o console backend (imprime o
# email no stdout do runserver) — permite testar o fluxo de reset sem
# credencial real do ZeptoMail. Para testar contra o sandbox real do
# ZeptoMail em dev, basta setar EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
# e as credenciais no .env local.