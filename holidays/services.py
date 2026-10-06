"""Service de feriados: cache-aside sobre a API externa (feriadosapi.com).

O backoffice guarda o calendário oficial (nacional, estadual, municipal)
em `Feriado` e só consulta a API paga quando o par ibge/ano ainda não foi
buscado com sucesso (`FeriadoBusca`). O gateway da clínica consome o
resultado via `GET /api/holidays/?ibge=&year=`.
"""

import logging

from django.conf import settings
from django.db import transaction
from django.db.models import Q, QuerySet

from .models import Feriado, FeriadoBusca
from .providers import ApiHolidayProvider, HolidayFetchResult, uf_from_ibge

logger = logging.getLogger(__name__)

# Default "de mentira" historicamente usado em settings.py. Se a chave em
# runtime for esta (ou vazia), a env var não foi configurada no ambiente —
# chamar a API só geraria 401 silencioso, então tratamos como ausente.
PLACEHOLDER_API_KEYS: frozenset[str] = frozenset({"", "1231312"})


class HolidayService:
    """Orquestra a busca/cache de feriados por município e ano."""

    @classmethod
    def _get_provider(cls) -> ApiHolidayProvider:
        api_key = settings.API_HOLIDAY_KEY
        api_url = settings.API_HOLIDAY
        return ApiHolidayProvider(api_key, api_url)

    @classmethod
    def api_key_configured(cls) -> bool:
        """Indica se `API_HOLIDAY_KEY` foi de fato configurada no ambiente.

        Returns:
            True se a chave não está vazia nem igual ao placeholder.
        """
        api_key = getattr(settings, "API_HOLIDAY_KEY", "") or ""
        return api_key.strip() not in PLACEHOLDER_API_KEYS

    @classmethod
    def already_fetched(cls, ibge_code: str, year: int) -> bool:
        """Indica se o calendário deste ibge/ano já foi buscado com sucesso."""
        return FeriadoBusca.objects.filter(ibge_code=ibge_code, year=year).exists()

    @classmethod
    def ensure_cached(cls, ibge_code: str, year: int) -> bool:
        """Garante que o calendário do ibge/ano esteja no banco local.

        Idempotente: se já buscado com sucesso, não chama a API. Se a busca
        falhar (ou vier incompleta), grava o que for aproveitável mas NÃO
        marca como buscado — a próxima chamada tenta de novo.

        Args:
            ibge_code: Código IBGE do município (7 dígitos).
            year: Ano de referência.

        Returns:
            True se, ao final, o par ibge/ano está marcado como buscado.
        """
        if cls.already_fetched(ibge_code, year):
            return True

        if not cls.api_key_configured():
            # [SECURITY] Nunca logar o valor da chave — só o fato de estar ausente.
            logger.error(
                "API_HOLIDAY_KEY ausente ou com valor placeholder; feriados não "
                "buscados (ibge=%s, ano=%s). Configure a env var no ambiente.",
                ibge_code, year,
            )
            return False

        result = cls._get_provider().fetch_holidays(ibge_code, year)
        cls._persist(ibge_code, year, result)

        if not result.complete:
            logger.warning(
                "Busca de feriados incompleta; não marcada como concluída "
                "(ibge=%s, ano=%s, registros=%s)",
                ibge_code, year, len(result.feriados),
            )
            return False
        return True

    @classmethod
    def _persist(cls, ibge_code: str, year: int, result: HolidayFetchResult) -> None:
        """Grava os feriados sem duplicar e marca a busca se completa.

        A `unique_together (date, type, ibge_code)` de `Feriado` não protege
        NACIONAL/ESTADUAL: `ibge_code` é NULL neles e NULLs são distintos
        em UNIQUE (Postgres e SQLite). Sem a deduplicação manual abaixo,
        cada município novo regravaria todos os nacionais e o endpoint
        devolveria feriados repetidos.
        """
        municipio_uf = uf_from_ibge(ibge_code)

        with transaction.atomic():
            for f in result.feriados:
                tipo = f['tipo']
                uf: str | None
                ibge: str | None
                if tipo == 'MUNICIPAL':
                    uf, ibge = f.get('uf') or municipio_uf, ibge_code
                    lookup = {'type': tipo, 'date': f['data'], 'ibge_code': ibge}
                elif tipo == 'ESTADUAL':
                    uf, ibge = f.get('uf') or municipio_uf, None
                    lookup = {'type': tipo, 'date': f['data'], 'uf': uf}
                else:
                    uf, ibge = None, None
                    lookup = {'type': tipo, 'date': f['data'], 'ibge_code__isnull': True}

                if Feriado.objects.filter(**lookup).exists():
                    continue
                Feriado.objects.create(
                    date=f['data'],
                    name=f['nome'],
                    type=tipo,
                    uf=uf,
                    ibge_code=ibge,
                    year=year,
                    description=f.get('descricao'),
                )

            if result.complete:
                FeriadoBusca.objects.update_or_create(ibge_code=ibge_code, year=year)

    @classmethod
    def calendar_queryset(cls, ibge_code: str, year: int) -> QuerySet[Feriado]:
        """Feriados que valem para o município: nacionais + estaduais da UF + municipais."""
        filtro = Q(type='NACIONAL') | Q(type='MUNICIPAL', ibge_code=ibge_code)
        uf = uf_from_ibge(ibge_code)
        if uf:
            filtro |= Q(type='ESTADUAL', uf=uf)
        return Feriado.objects.filter(year=year).filter(filtro)

    @classmethod
    def find_calendar_complet(cls, ibge_code: str, year: int) -> QuerySet[Feriado]:
        """Busca feriados municipais, estaduais e nacionais para um ano e código IBGE.

        Cache-aside: consulta a API só na primeira vez (ou enquanto as
        tentativas anteriores falharam); depois disso responde do banco.

        Args:
            ibge_code: Código IBGE do município.
            year: Ano para o qual buscar os feriados.

        Returns:
            QuerySet de `Feriado` aplicáveis ao município no ano.
        """
        cls.ensure_cached(ibge_code, year)
        return cls.calendar_queryset(ibge_code, year)
