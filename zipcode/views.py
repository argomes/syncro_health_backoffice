from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from syncro_backoffice.throttling import ReferenceDataRateThrottle

from .services import CepService


class CepSearchView(APIView):
    """
    GET /api/ceps/?logradouro=<logradouro>
    GET /api/ceps/?cep=<cep>

    Busca de CEPs brasileiros: por `cep` exato (cache-aside, consulta
    API externa via ViaCEP em caso de cache miss e persiste o
    resultado) ou por `logradouro` (autocomplete só na base local já
    cacheada, sem chamada externa). Pensado para o gateway Go de cada
    clínica (BACFF-015) resolver endereço a partir do CEP sem precisar
    de uma chamada direta à API externa a partir do desktop client.

    Decisão de autenticação (BACFF-015): endpoint PÚBLICO (`AllowAny`).
    Município/código IBGE/UF é dado público de referência geográfica
    (fonte: IBGE), não PII e não específico de nenhuma clínica — mesma
    classe de dado não-sensível já decidida para o branding público em
    EDGW-058. Diferente de `get_license_info`/`ClinicViewSet` (que expõem
    dado da CLÍNICA e por isso exigem Service Token / license_key), esta
    lista é global e idêntica para todos os tenants: não há isolamento a
    proteger aqui, então exigir autenticação só adicionaria fricção ao
    gateway sem nenhum ganho de segurança real.

    Rate limit (achado da revisão de código do app `zipcode`, 2026-08-02):
    endpoint público sem autenticação, então herdaria só o AnonRateThrottle
    default (60/min por IP) — insuficiente pra diferenciar uso legítimo do
    gateway (cache-aside, maioria dos CEPs repete) de um cliente mandando
    CEP inválido/aleatório em volume, que sempre dá cache miss e vira N
    chamadas à API externa do ViaCEP sem nenhum cache absorvendo a carga.
    Mesma classe de risco (dado público, risco é carga/disponibilidade da
    dependência externa, não vazamento) já resolvida para os endpoints de
    referência TUSS/ANS em BACFF-AVULSA-03 — reaproveita a mesma
    `ReferenceDataRateThrottle` (30/min) em vez de criar uma nova.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ReferenceDataRateThrottle]

    def get(self, request):
        logradouro = request.query_params.get("logradouro")
        cep = request.query_params.get("cep")
        if not logradouro and not cep:
            return Response({"erro": "the parameter 'logradouro' or 'cep' is necessary."}, status=400)

        if logradouro:
            resultados = CepService.buscar_logradouro(logradouro)
        else:
            resultados = CepService.buscar_cep(cep)

        return Response(resultados)
