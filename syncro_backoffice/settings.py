import os
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
API_HOLIDAY =  env('API_URL', default="https://feriadosapi.com/api")
API_HOLIDAY_KEY = env('API_HOLIDAY_KEY', default="1231312")
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
    'rest_framework_simplejwt.token_blacklist',
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
    'holidays',
    'municipios',
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
        # DIRS (filesystem loader) é consultado antes do app_directories
        # loader, então templates/admin/index.html aqui sobrescreve com
        # segurança o template do Unfold sem risco de recursão em
        # {% extends %} — o Unfold estende 'admin/base.html', não a si
        # mesmo, então nosso override também pode estender 'admin/base.html'
        # diretamente.
        'DIRS': [BASE_DIR / 'templates'],
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

# Host:porta do cluster Postgres (sem credencial embutida) — usado por
# portal_gestor/clinic_db.py para conectar como o db_user escopado da clínica,
# autenticado com a senha do grant temporário (BACFF-AVULSA-06). Nunca contém
# a credencial de superuser — essa vive só em PROVISIONING_DATABASE_URL, usada
# exclusivamente em clinics/provisioning.py.
PROVISIONING_HOST = env('PROVISIONING_HOST', default='localhost')
PROVISIONING_PORT = env.int('PROVISIONING_PORT', default=5432)

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
        # BACFF-AVULSA-06 (correção 2026-07-17): db-access-grant grava uma
        # senha de banco Postgres válida em cache. É uma ação administrativa
        # deliberada e rara (não uso frequente), então a rate é
        # deliberadamente baixa — recomendação do Security Engineer:
        # 5-10/hora por IP.
        'db_access_grant': '8/hour',
        # BACFF-AVULSA-03 (2026-07-20): endpoints de referência TUSS/ANS
        # (dados públicos) — rate acima do default anon, mas ainda contida,
        # para não travar o cache-aside do gateway sob uso legítimo.
        'reference_data': '30/minute',
        # EDGW-052 (Security Engineer, 2026-07-27): reportar problema é ação
        # rara e legítima em baixo volume — rate baixa por IP evita flood de
        # tickets falsos no Zoho Desk via license_key vazada.
        'error_report': '15/hour',
    },
}

# ── JWT ───────────────────────────────────────────────────────────────────────
# BACFF-004: ACCESS_TOKEN_LIFETIME reduzido de 8h para 2h. Não encontramos,
# neste repositório, um interceptor de refresh automático (silent refresh em
# 401) no frontend do backoffice — não há um cliente SPA correspondente aqui
# (o portal_gestor é servido via templates Django + cookies httpOnly, e o
# único código React encontrado é um scaffold de template não relacionado ao
# produto). Sem confirmação de refresh transparente, 30min quebraria a UX de
# um admin logado; 2h é um meio-termo até o refresh automático existir e
# permitir reduzir ainda mais.
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=2),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
}

# ── CORS ──────────────────────────────────────────────────────────────────────
# BACFF-007: CORS_ALLOW_ALL_ORIGINS nunca deve depender de DEBUG — se DEBUG=True
# vazar para staging/produção (erro comum de .env mal gerenciado), CORS ficaria
# totalmente aberto, permitindo requisição cross-origin autenticada usando as
# credenciais do navegador do admin logado. Fixo em False; origens confiáveis
# vêm explicitamente de CORS_ALLOWED_ORIGINS (env). Em dev local, configure
# CORS_ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000 no .env.
CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=[])
CORS_ALLOW_ALL_ORIGINS = False
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
    # ADMIN-DASHBOARD-REDESIGN (2026-07): substitui a index padrão do
    # Django admin (que com show_all_applications=False fica praticamente
    # vazia) pelos cards de "Dashboard de Serviços" com dado real já
    # existente (clientes ativos/inativos, gateways online, erros 24h).
    # Ver syncro_backoffice/dashboard.py e .claude/tasks/ADMIN-DASHBOARD-REDESIGN.md.
    "DASHBOARD_CALLBACK": "syncro_backoffice.dashboard.dashboard_callback",
    # TASK-057 + ADMIN-DASHBOARD-REDESIGN: busca geral (Cmd+K) cobrindo os
    # models que um analista/admin mais procura no dia a dia.
    "COMMAND": {
        "search_models": [
            "clinics.Clinic",
            "metrics.SystemHeartbeat",
            "support.Ticket",
            "billing.Invoice",
            "accounts.ClinicUser",
        ],
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        # ADMIN-DASHBOARD-REDESIGN (2026-07): reorganização em 5 grupos —
        # o menu tinha só 8 links pra 20 ModelAdmins registrados; os outros
        # 12 existiam só por URL direta (nenhum humano os encontrava pelo
        # menu). Ver .claude/tasks/ADMIN-DASHBOARD-REDESIGN.md §3 pra
        # inventário completo e racional de cada grupo. Cada item usa
        # `permission` — o Unfold já esconde item sem permissão, não precisa
        # de lógica extra aqui.
        "navigation": [
            {
                "title": "Principal",
                "separator": True,
                "items": [
                    {
                        "title": "Dashboard de Serviços",
                        "icon": "dashboard",
                        "link": reverse_lazy("admin:index"),
                        "permission": lambda request: request.user.is_staff,
                    },
                ],
            },
            {
                "title": "Administrar",
                "separator": True,
                "items": [
                    {
                        "title": "Clientes",
                        "icon": "business",
                        "link": reverse_lazy("admin:clinics_clinic_changelist"),
                        "permission": lambda request: request.user.has_perm("clinics.view_clinic"),
                    },
                    {
                        "title": "Usuários do cliente",
                        "icon": "group",
                        "link": reverse_lazy("admin:accounts_clinicuser_changelist"),
                        "permission": lambda request: request.user.has_perm("accounts.view_clinicuser"),
                    },
                    {
                        "title": "Faturas",
                        "icon": "receipt_long",
                        "link": reverse_lazy("admin:billing_invoice_changelist"),
                        "permission": lambda request: request.user.has_perm("billing.view_invoice"),
                    },
                    {
                        "title": "Planos",
                        "icon": "sell",
                        "link": reverse_lazy("admin:billing_plan_changelist"),
                        "permission": lambda request: request.user.has_perm("billing.view_plan"),
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
                        "title": "TISS · Lotes",
                        "icon": "folder_zip",
                        "link": reverse_lazy("admin:tiss_tisslote_changelist"),
                        "permission": lambda request: request.user.has_perm("tiss.view_tisslote"),
                    },
                    {
                        "title": "TISS · Guias",
                        "icon": "description",
                        "link": reverse_lazy("admin:tiss_tissguia_changelist"),
                        "permission": lambda request: request.user.has_perm("tiss.view_tissguia"),
                    },
                    {
                        "title": "TISS · Glosas",
                        "icon": "rule",
                        "link": reverse_lazy("admin:tiss_tissglosa_changelist"),
                        "permission": lambda request: request.user.has_perm("tiss.view_tissglosa"),
                    },
                    {
                        "title": "Elegibilidade",
                        "icon": "verified",
                        "link": reverse_lazy("admin:tiss_tisselegibilidadeconsulta_changelist"),
                        "permission": lambda request: request.user.has_perm("tiss.view_tisselegibilidadeconsulta"),
                    },
                    {
                        "title": "Relatórios",
                        "icon": "bar_chart",
                        "link": reverse_lazy("admin:portal_gestor_reportsession_changelist"),
                        "permission": lambda request: request.user.has_perm("portal_gestor.view_reportsession"),
                    },
                    {
                        "title": "Suporte",
                        "icon": "support_agent",
                        "link": reverse_lazy("admin:support_ticket_changelist"),
                        "permission": lambda request: request.user.has_perm("support.view_ticket"),
                    },
                    {
                        "title": "Avisos de produto",
                        "icon": "campaign",
                        "link": reverse_lazy("admin:portal_gestor_productnotice_changelist"),
                        "permission": lambda request: request.user.has_perm("portal_gestor.view_productnotice"),
                    },
                ],
            },
            {
                "title": "Configurações",
                "separator": True,
                "items": [
                    {
                        "title": "Feriados",
                        "icon": "event",
                        "link": reverse_lazy("admin:holidays_feriado_changelist"),
                        "permission": lambda request: request.user.has_perm("holidays.view_feriado"),
                    },
                    {
                        "title": "Operadoras ANS",
                        "icon": "corporate_fare",
                        "link": reverse_lazy("admin:tiss_ansinsuranceoperator_changelist"),
                        "permission": lambda request: request.user.has_perm("tiss.view_ansinsuranceoperator"),
                    },
                    {
                        "title": "Credenciais de operadora",
                        "icon": "key",
                        "link": reverse_lazy("admin:tiss_tissoperatorconfig_changelist"),
                        "permission": lambda request: request.user.has_perm("tiss.view_tissoperatorconfig"),
                    },
                    {
                        "title": "Tabela TUSS",
                        "icon": "table_view",
                        "link": reverse_lazy("admin:tiss_tussprocedurecode_changelist"),
                        "permission": lambda request: request.user.has_perm("tiss.view_tussprocedurecode"),
                    },
                    {
                        "title": "Municípios (IBGE)",
                        "icon": "map",
                        "link": reverse_lazy("admin:municipios_municipio_changelist"),
                        "permission": lambda request: request.user.has_perm("municipios.view_municipio"),
                    },
                    # CBO e CID-10 não existem neste repo (dado do Edge
                    # Gateway hoje) — feature nova, fora de escopo desta
                    # reorganização. Ver ADMIN-DASHBOARD-REDESIGN.md §3.1.
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
                        "title": "Auditoria de leitura (LGPD)",
                        "icon": "policy",
                        "link": reverse_lazy("admin:portal_gestor_portalreadauditlog_changelist"),
                        "permission": lambda request: request.user.has_perm("portal_gestor.view_portalreadauditlog"),
                    },
                    {
                        "title": "Acessos de suporte",
                        "icon": "badge",
                        "link": reverse_lazy("admin:accounts_clinicaccess_changelist"),
                        "permission": lambda request: request.user.has_perm("accounts.view_clinicaccess"),
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
    # NOTA (ADMIN-DASHBOARD-REDESIGN §1.2): o bloco "TABS" que existia aqui
    # apontava para "clinicas.clinica" / admin:clinics_clinica_changelist —
    # app label e nome de model errados (o real é "clinics" / "Clinic").
    # Nunca resolvia; era um KeyError-workaround morto, não uma feature em
    # uso. Removido nesta reorganização em vez de corrigido, pois nenhuma
    # tela hoje precisa de tabs de model — se surgir a necessidade, refazer
    # com o app_label/model corretos.
}

# LEGADO — Notion foi substituído pelo Zoho Desk (BACFF-AVULSA-07, Notion
# passou a cobrar pelo limite de 1000 blocos no plano gratuito). Mantido só
# para não quebrar leitura de tickets antigos que já têm notion_page_id;
# NotionService não é mais chamado no fluxo de sincronização ativo.
NOTION_API_KEY = env('NOTION_API_KEY', default='')
NOTION_DATABASE_ID = env('NOTION_DATABASE_ID', default='')

# Zoho Desk — integração ativa de sincronização de tickets (BACFF-AVULSA-07).
# Autenticação OAuth2 "self client" server-to-server: client_id/secret +
# refresh_token de longa duração gerados uma vez no Zoho API Console
# (console.zoho.com > Self Client), sem fluxo de login de usuário.
# ZOHO_DESK_ACCOUNTS_URL/API_BASE_URL variam por região da conta Zoho
# (.com / .eu / .in / .com.au / .jp) — ajustar no .env conforme a conta.
ZOHO_DESK_CLIENT_ID = env('ZOHO_DESK_CLIENT_ID', default='')
ZOHO_DESK_CLIENT_SECRET = env('ZOHO_DESK_CLIENT_SECRET', default='')
ZOHO_DESK_REFRESH_TOKEN = env('ZOHO_DESK_REFRESH_TOKEN', default='')
ZOHO_DESK_ORG_ID = env('ZOHO_DESK_ORG_ID', default='')
ZOHO_DESK_DEPARTMENT_ID = env('ZOHO_DESK_DEPARTMENT_ID', default='')
ZOHO_DESK_ACCOUNTS_URL = env('ZOHO_DESK_ACCOUNTS_URL', default='https://accounts.zoho.com')
ZOHO_DESK_API_BASE_URL = env('ZOHO_DESK_API_BASE_URL', default='https://desk.zoho.com/api/v1')

# Zoho Desk Webhook (BACFF-AVULSA-10) — caminho de VOLTA (Zoho -> Backoffice).
# A sincronização acima (create_ticket/update_ticket/add_comment) só empurra
# updates DO Backoffice PRO Zoho. Quando o time de suporte responde direto
# no painel do Zoho Desk, isso nunca chegava de volta aqui — o admin da
# clínica veria o ticket "aberto pra sempre" sem a resposta. Este token
# autentica o webhook configurado manualmente em Setup > Automation >
# Webhooks no painel Zoho Desk, evento "Add Comment" (ticket_comment.add),
# apontando pra /api/support/webhooks/zoho-comment/. Ver
# support/webhook_views.py para o corpo (JSON) esperado com os merge fields.
ZOHO_DESK_WEBHOOK_SECRET = env('ZOHO_DESK_WEBHOOK_SECRET', default='')

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
import sys

TESTING = 'test' in sys.argv or 'pytest' in sys.modules

CACHE_URL = env('CACHE_URL', default='')
if TESTING:
    # Teste unitário nunca deve depender de Redis real rodando na máquina —
    # LocMemCache isola cada execução e evita falha por infra ausente,
    # independente do que CACHE_URL apontar no .env local.
    CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
elif CACHE_URL:
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
#
# BACFF-010: antes, o default era `DEBUG` — se DEBUG=True vazasse para
# produção (mesma classe de erro já corrigida em CACHE_URL/TISS_FERNET_KEY/
# CORS_ALLOW_ALL_ORIGINS), tasks Celery (sync com Edge, Notion) passariam a
# rodar sincronamente na thread HTTP, podendo causar timeout de requisição
# do usuário sem nenhum aviso. Mesmo padrão de fail-fast: em produção
# (DEBUG=False), a variável precisa estar explicitamente definida no
# ambiente — nunca depender implicitamente de DEBUG.
if not DEBUG and 'CELERY_TASK_ALWAYS_EAGER' not in os.environ:
    raise RuntimeError(
        'CELERY_TASK_ALWAYS_EAGER não configurado explicitamente com '
        'DEBUG=False. Defina CELERY_TASK_ALWAYS_EAGER=False no ambiente de '
        'produção (broker Redis real) — não deve depender implicitamente '
        'de DEBUG, sob risco de tasks rodarem síncronas na thread HTTP.'
    )
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
# por tiss/xml_validator.py para validar o lote antes do envio SOAP.
# Default: schemas versionados dentro do próprio repo (funciona em
# dev/CI/produção sem configuração externa). TISS_XSD_DIR permite apontar
# para outra versão/local (ex.: atualização futura do padrão TISS) sem
# precisar alterar código.
TISS_XSD_DIR = env(
    'TISS_XSD_DIR',
    default=str(BASE_DIR / 'tiss' / 'schemas' / 'ans_tiss_v4_02_00'),
)

# BACFF-014 — versão do padrão TISS usada nas chamadas ao Autorize da
# Orizon (`tiss/orizon_autorize_client.py`/`orizon_autorize_xml_builder.py`).
# Manual oficial confirma que o WS aceita 4.01.00/4.02.00/4.03.00 — era
# hardcoded em '4.01.00' (achado 1, atualização 2026-07-29 do BACFF-014).
# Parametrizável para não exigir alterar código quando a Orizon atualizar o
# padrão aceito (mesmo racional de TISS_XSD_DIR acima).
TISS_PADRAO_VERSAO_ORIZON = env('TISS_PADRAO_VERSAO_ORIZON', default='4.03.00')

# Quando True, tiss/soap_client.py intercepta a chamada SOAP e devolve uma
# resposta fixa (sucesso ou erro) em vez de bater na rede — permite testar
# o fluxo completo de envio de lote sem credenciais/sandbox de operadora real.
#
# BACFF-012: antes, o default era `DEBUG` — mesma classe de erro já corrigida
# em CACHE_URL/TISS_FERNET_KEY/CORS_ALLOW_ALL_ORIGINS/CELERY_TASK_ALWAYS_EAGER:
# se DEBUG=True vazasse para produção, o cliente SOAP de TISS passaria a
# devolver respostas mockadas silenciosamente em vez de bater na rede real da
# operadora — sem nenhum erro visível. Mesmo padrão de fail-fast: em produção
# (DEBUG=False), a variável precisa estar explicitamente definida no ambiente
# — nunca deve depender implicitamente de DEBUG.
if not DEBUG and 'TISS_SOAP_MOCK' not in os.environ:
    raise RuntimeError(
        'TISS_SOAP_MOCK não configurado explicitamente com DEBUG=False. '
        'Defina TISS_SOAP_MOCK=False no ambiente de produção (SOAP real da '
        'operadora) — não deve depender implicitamente de DEBUG, sob risco '
        'de lotes TISS serem "enviados" apenas de forma mockada, sem bater '
        'na rede real da operadora.'
    )
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