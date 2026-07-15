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
fi

exec "$@"
