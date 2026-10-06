"""Pré-carrega o calendário de feriados de municípios para um ano."""

import re
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from holidays.services import HolidayService

IBGE_RE = re.compile(r"^\d{7}$")


class Command(BaseCommand):
    """Popula `Feriado` para os municípios informados, antes do gateway pedir.

    Por que existe: o cache é sob demanda (só grava quando o gateway chama
    `GET /api/holidays/`), então uma clínica nova começa sem feriados até
    a primeira sincronização — e qualquer falha da API fica invisível.
    Este comando permite carregar e verificar o calendário de forma
    explícita (ex.: no onboarding ou na virada do ano).

    `Clinic` ainda não guarda município/IBGE, por isso não há
    `--all-clinics`: os códigos são passados via `--ibge` (repetível).

    Idempotente: municípios já buscados com sucesso não chamam a API de
    novo (`FeriadoBusca`). Sai com erro se algum município falhar, para
    que a falha seja visível no deploy/cron.

    Uso:
        python manage.py preload_holidays --year 2026 --ibge 3550308 --ibge 3534401
    """

    help = "Pré-carrega feriados (nacionais, estaduais e municipais) por código IBGE e ano."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--year", type=int, required=True, help="Ano de referência (ex.: 2026).")
        parser.add_argument(
            "--ibge",
            action="append",
            required=True,
            help="Código IBGE do município (7 dígitos). Pode ser repetido.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        year: int = options["year"]
        ibge_codes: list[str] = list(dict.fromkeys(c.strip() for c in options["ibge"]))

        invalid = [c for c in ibge_codes if not IBGE_RE.match(c)]
        if invalid:
            raise CommandError(f"Código(s) IBGE inválido(s) (esperado 7 dígitos): {', '.join(invalid)}")

        if not HolidayService.api_key_configured():
            raise CommandError("API_HOLIDAY_KEY não configurada; nada foi buscado.")

        failures: list[str] = []
        for ibge_code in ibge_codes:
            was_cached = HolidayService.already_fetched(ibge_code, year)
            ok = HolidayService.ensure_cached(ibge_code, year)
            total = HolidayService.calendar_queryset(ibge_code, year).count()
            if not ok:
                failures.append(ibge_code)
                self.stderr.write(f"[FALHA] {ibge_code}/{year}: busca incompleta ({total} feriados no banco)")
            elif was_cached:
                self.stdout.write(f"[CACHE] {ibge_code}/{year}: já carregado ({total} feriados)")
            else:
                self.stdout.write(self.style.SUCCESS(f"[OK] {ibge_code}/{year}: {total} feriados"))

        if failures:
            raise CommandError(f"Falha ao carregar feriados para: {', '.join(failures)}")
