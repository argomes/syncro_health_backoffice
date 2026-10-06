import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

# Mapeamento dos 2 primeiros dígitos do código IBGE de município para a UF.
# Módulo-level (e não só atributo do provider) porque o service também
# precisa da UF para filtrar os feriados ESTADUAIS do município no retorno.
UF_BY_IBGE_PREFIX: dict[str, str] = {
    '12': 'AC', '27': 'AL', '13': 'AM', '16': 'AP', '29': 'BA',
    '23': 'CE', '53': 'DF', '32': 'ES', '52': 'GO', '21': 'MA',
    '31': 'MG', '50': 'MS', '51': 'MT', '15': 'PA', '25': 'PB',
    '26': 'PE', '22': 'PI', '41': 'PR', '33': 'RJ', '24': 'RN',
    '43': 'RS', '11': 'RO', '14': 'RR', '42': 'SC', '28': 'SE',
    '35': 'SP', '17': 'TO',
}


def uf_from_ibge(ibge_code: str) -> str | None:
    """Deriva a UF a partir dos 2 primeiros dígitos do código IBGE.

    Args:
        ibge_code: Código IBGE do município (7 dígitos).

    Returns:
        A sigla da UF (ex.: "SP") ou None se o prefixo não for reconhecido.
    """
    return UF_BY_IBGE_PREFIX.get(str(ibge_code)[:2])


@dataclass
class HolidayFetchResult:
    """Resultado de uma busca de feriados na API externa.

    `complete` diz se a busca trouxe o calendário completo do município
    (endpoint de cidade respondeu 200 em todas as páginas). Quando False
    (falha de rede, status != 200, fallback nacional+estadual, paginação
    interrompida), o service grava o que veio mas NÃO marca o par ibge/ano
    como já buscado — assim a próxima chamada tenta de novo em vez de
    congelar um calendário sem os feriados municipais.
    """

    feriados: list[dict] = field(default_factory=list)
    complete: bool = False

class BaseHolidayProvider(ABC):
    """Interface abstrata que define como qualquer provedor de feriado deve se comportar"""
    
    @abstractmethod
    def find_municipal_holidays(self, ibge_code: str, year: int) -> list[dict]:
        """Return a list of municipal holidays for a given IBGE code and year."""
    
    
class ApiHolidayProvider(BaseHolidayProvider):
    """Holiday provider that fetches holiday data from an external API."""

    # Página máxima de segurança: evita loop infinito caso a API devolva um
    # `meta.total_pages` inconsistente/corrompido (nunca deveríamos precisar
    # de mais que isso pra feriados de um único município/ano).
    MAX_PAGES = 20

    # Mantido como alias por compatibilidade com código/testes existentes.
    _UF_BY_IBGE_PREFIX = UF_BY_IBGE_PREFIX

    def __init__(self, api_key: str, api_url: str):
        self.api_key = api_key
        self.api_base_url = api_url
        # Endpoint correto confirmado contra https://feriadosapi.com/docs.
        # O endpoint antigo (`/v1/municipio/{ibge}`) nunca retornava a chave
        # "feriados" — só metadados da cidade.
        self.api_url = f"{api_url}/v1/feriados/cidade/{{ibge}}"
        # [SECURITY] `api_key` idealmente deveria vir de um cofre/secret
        # manager em vez de settings.py em texto puro — fora do escopo deste
        # fix, mas sinalizando pra próxima revisão de segurança.

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

    def _fetch_paginated(self, url: str, params: dict) -> tuple[list[dict], bool]:
        """Busca todos os feriados de um endpoint paginado da API.

        Helper privado reaproveitado pelos três endpoints (`cidade`,
        `nacionais`, `estado`) — todos seguem o mesmo contrato de paginação
        (`meta.page`/`meta.total_pages`), então a lógica de loop/limite de
        segurança vive num único lugar em vez de replicada 3x.

        Se a primeira página falhar (erro de rede/timeout/status != 200),
        retorna lista vazia — não há dado parcial confiável ainda. Se uma
        página subsequente falhar, retorna o que já foi acumulado até ali
        em vez de descartar tudo, já que parte dos feriados já é válida e
        útil pro cache.

        Args:
            url (str): URL completa do endpoint (sem query params).
            params (dict): Query params fixos (ex.: {"ano": year}); `limit`
                e `page` são adicionados/atualizados internamente a cada
                iteração do loop de paginação.
        Returns:
            tuple[list[dict], bool]: Lista bruta (não padronizada) de
                feriados da API e um flag `ok` — False se QUALQUER página
                falhou (o resultado pode estar vazio ou parcial).
        """
        headers = {"Authorization": f"Bearer {self.api_key}"}
        feriados_brutos: list[dict] = []
        page = 1

        while page <= self.MAX_PAGES:
            request_params = {**params, "limit": 100, "page": page}
            try:
                response = requests.get(url, headers=headers, params=request_params, timeout=8)
                if response.status_code != 200:
                    # [LGPD] Só endpoint/status/página — nunca headers (contêm
                    # a API key) nem corpo da resposta.
                    logger.error(
                        "API de feriados respondeu status %s (endpoint=%s, params=%s, page=%s)",
                        response.status_code, url, params, page,
                    )
                    return feriados_brutos, False

                dados = response.json()
            except (requests.RequestException, ValueError) as exc:
                logger.error(
                    "Falha ao chamar API de feriados (endpoint=%s, params=%s, page=%s, erro=%s)",
                    url, params, page, type(exc).__name__,
                )
                return feriados_brutos, False

            feriados_brutos.extend(dados.get('feriados', []))

            meta = dados.get('meta', {})
            total_pages = meta.get('total_pages', 1)
            current_page = meta.get('page', page)

            if current_page >= total_pages:
                break

            page = current_page + 1

        return feriados_brutos, True

    def _derive_uf(self, ibge_code: str) -> str | None:
        """Deriva a sigla da UF a partir dos 2 primeiros dígitos do código IBGE.

        Args:
            ibge_code (str): Código IBGE do município (7 dígitos).
        Returns:
            str | None: A sigla da UF (ex.: "SP") ou None se o prefixo não
                for reconhecido (código malformado).
        """
        return uf_from_ibge(ibge_code)

    def _find_national_and_state_holidays(self, uf: str | None, year: int) -> list[dict]:
        """Busca feriados nacionais + estaduais (endpoints 100% gratuitos, sem cota).

        Usado como fallback quando o endpoint de cidade falha (erro de rede,
        status != 200, ou cota mensal estourada para município não-capital)
        — garante que a clínica nunca fique com ZERO feriados só porque a
        chamada municipal específica falhou, já que nacional/estadual não
        dependem dessa cota.

        Args:
            uf (str | None): Sigla da UF do município. Se None (prefixo IBGE
                não reconhecido), busca só os nacionais.
            year (int): Ano de referência.
        Returns:
            list[dict]: Feriados nacionais + estaduais (brutos, não padronizados).
        """
        feriados_brutos, _ = self._fetch_paginated(
            f"{self.api_base_url}/v1/feriados/nacionais", {"ano": year}
        )

        if uf:
            estaduais, _ = self._fetch_paginated(
                f"{self.api_base_url}/v1/feriados/estado/{uf}", {"ano": year}
            )
            feriados_brutos += estaduais

        return feriados_brutos

    def find_municipal_holidays(self, ibge_code: str, year: int) -> list[dict]:
        """Fetch municipal holidays for a given IBGE code and year from the API.

        A API pagina os resultados (padrão 50 itens/página, máximo 100).
        Usamos `limit=100` pra minimizar o número de chamadas — cada request
        pra município não-capital consome 1 unidade da cota mensal.

        Se a chamada ao endpoint de cidade falhar por QUALQUER motivo (status
        != 200, exceção de rede, cota mensal estourada), caímos pro fallback
        de nacional + estadual (`_find_national_and_state_holidays`), que são
        gratuitos e sem limite de cota. Preferimos "tentar município primeiro
        e cair pro fallback só em falha" em vez de "pular direto pro fallback
        pra não-capitais": assim, enquanto a cota do mês não estourar, a
        clínica continua recebendo os feriados municipais específicos (que
        não têm alternativa gratuita nenhuma). Só perdemos o municipal
        quando a chamada de fato falha — nunca ficamos com lista vazia.

        Args:
            ibge_code (str): The IBGE code of the municipality.
            year (int): The year for which to fetch holidays.
        Returns:
            list[dict]: A list of dictionaries representing the holidays
                (municipais em caso de sucesso; nacionais+estaduais em caso
                de fallback; lista vazia só se TUDO falhar).
        """
        return self.fetch_holidays(ibge_code, year).feriados

    def fetch_holidays(self, ibge_code: str, year: int) -> HolidayFetchResult:
        """Busca o calendário do município e informa se ele veio completo.

        Mesma estratégia de `find_municipal_holidays` (cidade primeiro,
        fallback nacional+estadual só se a cidade não trouxer nada), mas
        expõe `complete` para o service decidir se pode marcar o par
        ibge/ano como já buscado.

        Args:
            ibge_code: Código IBGE do município.
            year: Ano de referência.

        Returns:
            HolidayFetchResult com os feriados padronizados e o flag
            `complete` (True só se o endpoint de cidade respondeu 200 em
            todas as páginas).
        """
        url = self.api_url.format(ibge=ibge_code)
        feriados_brutos, ok = self._fetch_paginated(url, {"ano": year})

        if not feriados_brutos:
            uf = self._derive_uf(ibge_code)
            logger.warning(
                "Endpoint de cidade sem feriados (ibge=%s, ano=%s, ok=%s); "
                "usando fallback nacional+estadual (uf=%s)",
                ibge_code, year, ok, uf,
            )
            feriados_brutos = self._find_national_and_state_holidays(uf, year)
            # Fallback nunca é "completo": faltam os municipais, e o endpoint
            # de cidade sempre devolve ao menos os nacionais quando funciona.
            ok = False

        return HolidayFetchResult(feriados=self._padronizar(feriados_brutos), complete=ok)
