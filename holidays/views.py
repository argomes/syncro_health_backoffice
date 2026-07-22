from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .services import HolidayService

class ListarFeriadosClinicaView(APIView):
    permission_classes = [IsAuthenticated]
    
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