#!/bin/sh
# Entrypoint da imagem de produção do backoffice (Dockerfile na raiz do repo).
#
# Só roda migrate/collectstatic quando o processo principal é o servidor web
# (gunicorn) — o worker Celery usa a MESMA imagem com CMD diferente, e rodar
# migrate a partir de dois containers simultâneos no boot é uma corrida
# desnecessária (Django não garante migrations concorrentes seguras). Deixa
# o container do gunicorn ser o único responsável por isso.
set -u

# 2026-08-10: substituído "set -e" por checagem explícita (if ! cmd) em cada
# passo. Motivo: em pelo menos uma falha real no Railway (migrate crashando
# em tiss.0005_seed_tuss22_completo por DataError), o traceback completo do
# Python NUNCA apareceu nos "deployment logs" via `railway logs` — só foi
# possível ver o erro real rodando a mesma imagem localmente via `docker
# run` com as credenciais reais. Suspeita: o container morre rápido demais
# (exit imediato via "set -e") para o coletor de log do Railway terminar de
# capturar o stdout antes do processo ser encerrado/removido. As mudanças
# abaixo (echo com marcador bem visível + `sleep 3` antes de sair) dão
# tempo pro pipeline de log flushar mesmo que a causa raiz seja essa —
# mitigação, não elimina a possibilidade de a plataforma descartar logs de
# um deploy que nunca fica "healthy", mas é o que dá pra controlar do lado
# da aplicação.

if [ "$1" = "gunicorn" ]; then
    echo "[entrypoint] aplicando migrations..."
    if ! python manage.py migrate --noinput 2>&1; then
        echo "[entrypoint] ============================================"
        echo "[entrypoint] ERRO FATAL: migrate falhou (traceback acima)."
        echo "[entrypoint] Abortando boot — gunicorn NÃO vai subir."
        echo "[entrypoint] ============================================"
        sleep 3
        exit 1
    fi

    echo "[entrypoint] coletando static files..."
    if ! python manage.py collectstatic --noinput 2>&1; then
        echo "[entrypoint] ============================================"
        echo "[entrypoint] ERRO FATAL: collectstatic falhou (traceback acima)."
        echo "[entrypoint] Abortando boot — gunicorn NÃO vai subir."
        echo "[entrypoint] ============================================"
        sleep 3
        exit 1
    fi

    # 2026-08-12: import_municipios nunca tinha rodado em produção — a tabela
    # ficou vazia desde sempre, quebrando silenciosamente a busca de município
    # no Setup Wizard do app desktop (endpoint respondia 200 com lista vazia,
    # nenhum erro visível em lugar nenhum). Comando é idempotente
    # (update_or_create por codigo_ibge), seguro rodar em todo boot — garante
    # que a tabela nunca fica vazia de novo, mesmo após reset de banco.
    echo "[entrypoint] importando/atualizando base de municípios (IBGE)..."
    if ! python manage.py import_municipios 2>&1; then
        echo "[entrypoint] AVISO: import_municipios falhou — não é fatal, mas a"
        echo "[entrypoint] busca de município no app vai ficar sem resultados."
    fi

    # Railway injeta $PORT dinamicamente (não necessariamente 8000 — em
    # produção observado 8080) e roteia o healthcheck/tráfego pra essa
    # porta. $PORT ausente (docker-compose local/VPS) cai para 8000, mesmo
    # default de sempre — não quebra esses ambientes.
    echo "[entrypoint] iniciando gunicorn na porta ${PORT:-8000}..."
    exec gunicorn syncro_backoffice.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --workers 3 --timeout 30
fi

exec "$@"
