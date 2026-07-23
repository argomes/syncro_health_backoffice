from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import MunicipioService


class MunicipioSearchView(APIView):
    """
    GET /api/municipios/?q=<termo>

    Autocomplete de municípios brasileiros, retornando `codigo_ibge`,
    `nome`, `uf` e `timezone` (já resolvido a partir da UF) em uma única
    chamada — pensado para o gateway Go de cada clínica (EDGW-059)
    resolver o fuso horário da clínica sem precisar de uma segunda
    tabela local nem de uma segunda requisição.

    Decisão de autenticação (BACFF-015): endpoint PÚBLICO (`AllowAny`).
    Município/código IBGE/UF é dado público de referência geográfica
    (fonte: IBGE), não PII e não específico de nenhuma clínica — mesma
    classe de dado não-sensível já decidida para o branding público em
    EDGW-058. Diferente de `get_license_info`/`ClinicViewSet` (que expõem
    dado da CLÍNICA e por isso exigem Service Token / license_key), esta
    lista é global e idêntica para todos os tenants: não há isolamento a
    proteger aqui, então exigir autenticação só adicionaria fricção ao
    gateway sem nenhum ganho de segurança real.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        termo = request.query_params.get("q") or request.query_params.get("nome")
        if not termo:
            return Response({"erro": "the parameter 'q' is necessary."}, status=400)

        resultados = MunicipioService.buscar_por_nome(termo)
        return Response(resultados)
