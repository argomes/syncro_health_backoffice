#!/bin/sh
# Entrypoint da imagem de produção do backoffice (Dockerfile na raiz do repo).
#
# Só roda migrate/collectstatic quando o processo principal é o servidor web
# (gunicorn) — o worker Celery usa a MESMA imagem com CMD diferente, e rodar
# migrate a partir de dois containers simultâneos no boot é uma corrida
# desnecessária (Django não garante migrations concorrentes seguras). Deixa
# o container do gunicorn ser o único responsável por isso.
set -eu

if [ "$1" = "gunicorn" ]; then
    echo "[entrypoint] aplicando migrations..."
    python manage.py migrate --noinput

    echo "[entrypoint] coletando static files..."
    python manage.py collectstatic --noinput

    # 2026-08-10: Railway injeta $PORT dinamicamente (não necessariamente
    # 8000 — em produção observado 8080) e roteia o healthcheck/tráfego pra
    # essa porta. O bind hardcoded em "0.0.0.0:8000" no CMD do Dockerfile
    # fazia o gunicorn escutar numa porta que o Railway nunca batia —
    # "1/1 replicas never became healthy" com "service unavailable" em
    # TODAS as tentativas, mesmo com healthcheckTimeout alto (não era
    # timeout, era porta errada). $PORT ausente (docker-compose local/VPS)
    # cai para 8000, mesmo default de sempre — não quebra esses ambientes.
    echo "[entrypoint] iniciando gunicorn na porta ${PORT:-8000}..."
    exec gunicorn syncro_backoffice.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --workers 3 --timeout 30
fi

exec "$@"
