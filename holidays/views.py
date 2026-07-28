from rest_framework.views import APIView
from rest_framework.response import Response
from clinics.permissions import IsAuthenticatedByLicenseKey
from .services import HolidayService

class ListarFeriadosClinicaView(APIView):
    # EDGW-060: consumido pelo worker do gateway (chamada máquina-a-máquina,
    # sem sessão de usuário) via X-License-Key — mesmo mecanismo usado pelos
    # outros endpoints de referência do gateway (ver tiss/views.py,
    # clinics/permissions.py). IsAuthenticated (default, JWT de usuário) não
    # é utilizável pelo worker headless do gateway.
    permission_classes = [IsAuthenticatedByLicenseKey]
    
    def get(self, request):
        ibge_code = request.query_params.get('ibge')
        year = request.query_params.get('year')

        if not ibge_code or not year:
            return Response({"erro": "the parameter 'ibge' and 'year' is necessarys."}, status=400)

        feriados = HolidayService.find_calendar_complet(ibge_code=ibge_code, year=int(year))

        dados = [
            {"data": f.date.strftime("%Y-%m-%d"), "nome": f.name, "tipo": f.type, "uf": f.uf}
            for f in feriados
        ]
        return Response(dados)