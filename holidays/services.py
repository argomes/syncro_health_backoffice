from django.conf import settings
from django.db import transaction
from django.db.models import Q
from .models import Feriado
from .providers import ApiHolidayProvider

class HolidayService:
    @classmethod
    def _get_provider(cls):
        api_key = settings.API_HOLIDAY_KEY
        api_url = settings.API_HOLIDAY
        return ApiHolidayProvider(api_key, api_url)


    @classmethod
    def find_calendar_complet(cls, ibge_code:str, year:int) -> list[dict]:
        """Busca feriados municipais, estaduais e nacionais para um determinado ano e código IBGE.
        
        Args:
            ibge_code (str): Código IBGE do município.
            year (int): Ano para o qual buscar os feriados.
        Returns:
            list[dict]: List of dictionaries representing the holidays.
        """
    
        provider = cls._get_provider()
        cache_exists = Feriado.objects.filter(Q(ibge_code=ibge_code) & Q(year=year)).exists()
        
        if not cache_exists:
            municipal_holidays = provider.find_municipal_holidays(ibge_code, year)
            
            novos_registros = []
            for f in municipal_holidays:
                novos_registros.append(Feriado(
                        date=f['data'],
                        name=f['nome'],
                        type=f['tipo'],
                        uf=f['uf'] if f['tipo'] == 'MUNICIPAL' else None,
                        ibge_code=ibge_code if f['tipo'] == 'MUNICIPAL' else None,
                        year=year,
                        description=f.get('descricao', None),
                    ))
            with transaction.atomic():
                Feriado.objects.bulk_create(novos_registros, ignore_conflicts=True)
                
        
        return Feriado.objects.filter(year=year).filter(
            Q(type='NACIONAL') | Q(ibge_code=ibge_code))