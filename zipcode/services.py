from django.db import IntegrityError

from .models import Cep, normalizar_texto
from .providers import ApiCepProvider

MAX_RESULTS = 20


def normalizar_cep(cep: str) -> str:
    """
    Reduz o CEP a 8 dígitos, sem hífen/espaços.

    Achado na revisão: o cache-aside comparava `cep` bruto vindo do
    cliente (`Cep.objects.get(cep=cep)`) contra o valor já salvo, que
    vem formatado pela API externa (ex.: "01310-930"). Um gateway
    mandando "01310930" (sem hífen) nunca batia com o registro
    cacheado, então TODA consulta nesse formato ia parar na API
    externa de novo — cache-aside quebrado na prática, mesmo com dado
    já salvo no banco.
    """
    if not cep:
        return ""
    return "".join(c for c in cep if c.isdigit())


class CepService:
    """
    Busca de ceps — o gateway
    Go de cada clínica consome este serviço via `CepSearchView` numa
    única chamada, busca na API caso não tenha cadastrado na nossa base de dados.
    """

    @classmethod
    def buscar_cep(cls, cep: str) -> dict:
        """
        Busca um CEP na base de dados local, e caso não encontre, busca na API externa.
        """
        cep_normalizado = normalizar_cep(cep)
        if not cep_normalizado:
            return {}

        # Tenta buscar na base de dados local
        try:
            cep_obj = Cep.objects.get(cep=cep_normalizado)
            return cls._serialize(cep_obj)
        except Cep.DoesNotExist:
            # Caso não encontre, busca na API externa
            api_provider = ApiCepProvider(api_url="https://viacep.com.br")
            endereco = api_provider.find_cep(cep_normalizado)
            if not endereco:
                return {}

            # Salva o endereço na base de dados local para futuras consultas
            cep_obj = Cep(
                codigo_ibge=endereco.get("ibge") or "",
                cep=normalizar_cep(endereco.get("cep")) or cep_normalizado,
                logradouro=endereco.get("logradouro") or "",
                bairro=endereco.get("bairro") or "",
                localidade=endereco.get("localidade") or "",
                uf=endereco.get("uf") or "",
                gia=endereco.get("gia") or "",
                ddd=endereco.get("ddd") or "",
                siafi=endereco.get("siafi") or "",
            )
            try:
                cep_obj.save()
            except IntegrityError:
                # Corrida: outra requisição concorrente já persistiu o
                # mesmo `cep` entre o DoesNotExist acima e este save().
                # Idempotência: não é erro do chamador, apenas relê o
                # registro que o "vencedor" da corrida já gravou.
                cep_obj = Cep.objects.get(cep=cep_obj.cep)
            return cls._serialize(cep_obj)
    
    @classmethod
    def buscar_logradouro(cls, logradouro: str, limit: int = MAX_RESULTS) -> list[dict]:
        logradouro = (logradouro or "").strip()
        if not logradouro:
            return []

        limit = min(limit or MAX_RESULTS, MAX_RESULTS)
        logradouro_normalizado = normalizar_texto(logradouro)
        ceps = Cep.objects.filter(
            logradouro_normalizado__icontains=logradouro_normalizado
        ).order_by("logradouro")[:limit]

        return [cls._serialize(c) for c in ceps]

    @staticmethod
    def _serialize(cep: Cep) -> dict:
        return {
            "codigo_ibge": cep.codigo_ibge,
            "uf": cep.uf,
            "logradouro": cep.logradouro,
            "cep": cep.cep,
            "bairro": cep.bairro,
            "localidade": cep.localidade,
            "estado": cep.estado,
            "regiao": cep.regiao,
            "gia": cep.gia,
            "ddd": cep.ddd,
            "siafi": cep.siafi,
        }
