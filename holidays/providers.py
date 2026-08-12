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
    
    # Página máxima de segurança: evita loop infinito caso a API devolva um
    # `meta.total_pages` inconsistente/corrompido (nunca deveríamos precisar
    # de mais que isso pra feriados de um único município/ano).
    MAX_PAGES = 20

    def __init__(self, api_key: str, api_url: str):
        self.api_key = api_key
        # Endpoint correto confirmado contra https://feriadosapi.com/docs.
        # O endpoint antigo (`/v1/municipio/{ibge}`) nunca retornava a chave
        # "feriados" — só metadados da cidade.
        self.api_url = f"{api_url}/v1/feriados/cidade/{{ibge}}"

    @staticmethod
    def _padronizar(lista_feriados: list[dict]) -> list[dict]:
        """Converte os feriados retornados pela API para o formato interno."""
        feriados_padronizados = []
        for f in lista_feriados:
            feriados_padronizados.append({
                "data": datetime.strptime(f['data'], "%d/%m/%Y").date(),
                "nome": f['nome'],
                "tipo": f['tipo'],  # NACIONAL, MUNICIPAL, ESTADUAL
                "uf": f.get('uf', None),
                "codigo_ibge": f.get('codigo_ibge', None),
                "descricao": f.get('descricao', None),
                "bancario": f.get('bancario', False),
            })
        return feriados_padronizados

    def find_municipal_holidays(self, ibge_code: str, year: int) -> list[dict]:
        """Fetch municipal holidays for a given IBGE code and year from the API.

        A API pagina os resultados (padrão 50 itens/página, máximo 100).
        Usamos `limit=100` pra minimizar o número de chamadas — cada request
        pra município não-capital consome 1 unidade da cota mensal — mas o
        loop abaixo não assume que tudo cabe em 1 página: continua buscando
        enquanto `meta.page < meta.total_pages`.

        Se a primeira página falhar (erro de rede/timeout/status != 200),
        retornamos lista vazia — não há dado parcial confiável ainda. Se uma
        página subsequente falhar, retornamos o que já foi acumulado até ali
        em vez de descartar tudo, já que parte dos feriados já é válida e
        útil pro cache.

        Args:
            ibge_code (str): The IBGE code of the municipality.
            year (int): The year for which to fetch holidays.
        Returns:
            list[dict]: A list of dictionaries representing the municipal holidays.
        """
        url = self.api_url.format(ibge=ibge_code)
        headers = {"Authorization": f"Bearer {self.api_key}"}

        feriados_brutos: list[dict] = []
        page = 1

        while page <= self.MAX_PAGES:
            params = {"ano": year, "limit": 100, "page": page}
            try:
                response = requests.get(url, headers=headers, params=params, timeout=8)
                if response.status_code != 200:
                    break

                dados = response.json()
            except requests.RequestException:
                break

            feriados_brutos.extend(dados.get('feriados', []))

            meta = dados.get('meta', {})
            total_pages = meta.get('total_pages', 1)
            current_page = meta.get('page', page)

            if current_page >= total_pages:
                break

            page = current_page + 1

        return self._padronizar(feriados_brutos)