import requests
from abc import ABC, abstractmethod
from datetime import datetime

class BaseHolidayProvider(ABC):
    """Interface abstrata que define como qualquer provedor de feriado deve se comportar"""
    
    @abstractmethod
    def find_municipal_holidays(self, ibge_code: str, year: int) -> list[dict]:
        """Return a list of municipal holidays for a given IBGE code and year."""
        pass
    
    
class ApiHolidayProvider(BaseHolidayProvider):
    """Holiday provider that fetches holiday data from an external API."""
    
    def __init__(self, api_key: str, api_url: str):
        self.api_key = api_key
        self.api_url = f"{api_url}/v1/municipio/{{ibge}}"
        
    
    def find_municipal_holidays(self, ibge_code: str, year: int) -> list[dict]:
        """Fetch municipal holidays for a given IBGE code and year from the API.
        Args:
            ibge_code (str): The IBGE code of the municipality.
            year (int): The year for which to fetch holidays.
        Returns:
            list[dict]: A list of dictionaries representing the municipal holidays. 
        """
        
        url = self.api_url.format(ibge=ibge_code)
        headers = {"Authorization": f"Bearer {self.api_key}"}
        params = {"ano": year}
        try:
            response = requests.get(url, headers=headers, params=params, timeout=8)
            if response.status_code != 200:
                return []
            
            dados = response.json()
            lista_feriados = dados.get('feriados', [])
            uf = dados.get('cidade', {}).get('uf', '')

            # Padroniza o retorno para o formato que o nosso banco de dados espera
            feriados_padronizados = []
            for f in lista_feriados:
                feriados_padronizados.append({
                    "data": datetime.strptime(f['data'], "%d/%m/%Y").date(),
                    "nome": f['nome'],
                    "tipo": f['tipo'], # NACIONAL, MUNICIPAL, ESTADUAL
                    "uf": f.get('uf', None),
                    "codigo_ibge": f.get('codigo_ibge', None),
                    "descricao": f.get('descricao', None),
                    "bancario": f.get('bancario', False),
                })
            return feriados_padronizados

        except requests.RequestException:
            # Retorna lista vazia ou levanta uma exceção customizada capturada pelo service
            return []