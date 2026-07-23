from .models import Municipio, normalizar_texto
from .timezones import resolve_timezone

MAX_RESULTS = 20


class MunicipioService:
    """
    Busca de municípios com timezone já resolvido (BACFF-015) — o gateway
    Go de cada clínica consome este serviço via `MunicipioSearchView` numa
    única chamada, sem precisar de uma segunda tabela local de UF/timezone.
    """

    @classmethod
    def buscar_por_nome(cls, termo: str, limit: int = MAX_RESULTS) -> list[dict]:
        termo = (termo or "").strip()
        if not termo:
            return []

        limit = min(limit or MAX_RESULTS, MAX_RESULTS)
        termo_normalizado = normalizar_texto(termo)
        municipios = Municipio.objects.filter(
            nome_normalizado__icontains=termo_normalizado
        ).order_by("nome")[:limit]

        return [cls._serialize(m) for m in municipios]

    @staticmethod
    def _serialize(municipio: Municipio) -> dict:
        return {
            "codigo_ibge": municipio.codigo_ibge,
            "nome": municipio.nome,
            "uf": municipio.uf,
            "timezone": resolve_timezone(municipio.uf, municipio.codigo_ibge),
        }
