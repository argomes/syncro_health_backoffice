# Imagem de produção do backoffice Django — usada tanto no serviço web
# (gunicorn) quanto no worker Celery (mesma imagem, CMD diferente). Pensada
# pra rodar igual no Railway (build via Dockerfile) e numa VPS qualquer via
# docker-compose.prod.yml — nenhuma dependência específica de plataforma.
#
# Build em dois estágios: "builder" compila as dependências que precisam de
# toolchain (cryptography, psycopg2-binary já traz wheel, mas cryptography
# em slim images às vezes precisa de build), "runtime" fica só com o
# necessário pra rodar — imagem final bem menor, sem gcc/headers.

FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=syncro_backoffice.settings

WORKDIR /app

# libpq5 é a lib de runtime do psycopg2 (libpq-dev do builder só era
# necessário para compilar, não para rodar).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser

COPY --from=builder /install /usr/local
COPY . .
RUN chmod +x docker/entrypoint.sh && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# Usado pelo healthcheck do docker-compose.prod.yml e, no Railway, pelo
# healthcheckPath configurado no serviço (ver README de deploy).
# HEALTHCHECK aqui é best-effort para VPS puro (docker run sem orquestrador);
# Railway/compose sobrescrevem com sua própria configuração de healthcheck.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health/ || exit 1

ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["gunicorn", "syncro_backoffice.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "30"]
