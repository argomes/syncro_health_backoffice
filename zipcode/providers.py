"""https://viacep.com.br/ws/05869230/json/"""

import requests
from abc import ABC, abstractmethod
from datetime import datetime


class BaseCepProvider(ABC):
    """Interface abstrata que define como qualquer provedor de CEP deve se comportar"""
    
    @abstractmethod
    def find_cep(self, cep: str) -> dict:
        """Return a dictionary with address information for a given CEP."""
        pass
    
class ApiCepProvider(BaseCepProvider):
    """CEP provider that fetches address data from an external API."""
    
    def __init__(self, api_url: str):
        self.api_url = f"{api_url}/ws/{{cep}}/json/"
        
    def find_cep(self, cep: str) -> dict:
        """Fetch address information for a given CEP from the API.
        Args:
            cep (str): The CEP to look up.
        Returns:
            dict: A dictionary containing address information for the given CEP.
        """
        url = self.api_url.format(cep=cep)
        try:
            response = requests.get(url, timeout=8)
            if response.status_code != 200:
                return {}
            
            dados = response.json()
            if "erro" in dados:
                return {}
            
            # Padroniza o retorno para o formato que o nosso banco de dados espera
            endereco_padronizado = {
                "cep": dados.get("cep"),
                "logradouro": dados.get("logradouro"),
                "complemento": dados.get("complemento"),
                "bairro": dados.get("bairro"),
                "localidade": dados.get("localidade"),
                "uf": dados.get("uf"),
                "ibge": dados.get("ibge"),
                "gia": dados.get("gia"),
                "ddd": dados.get("ddd"),
                "siafi": dados.get("siafi"),
            }
            return endereco_padronizado

        except requests.RequestException:
            # Retorna dicionário vazio ou levanta uma exceção customizada capturada pelo service
            return {}