"""
Tabela estática UF -> timezone IANA (BACFF-015).

O Brasil tem apenas 4 fusos horários fixos e, desde 2019, não usa mais
horário de verão — não há necessidade de lógica de DST nem de
granularidade por município (municípios do mesmo UF sempre compartilham
o mesmo fuso). Por isso isto é um dict Python simples, sem tabela no
banco: não muda com o tempo e não precisa de migration para alterar.

Fontes: fusos fixados pelo Decreto nº 6.558/2008 (mantidos após o fim do
horário de verão em 2019) e IBGE (lista de UFs).
"""

# UF -> timezone IANA
TIMEZONE_BY_UF = {
    # America/Rio_Branco (UTC-5)
    'AC': 'America/Rio_Branco',

    # America/Manaus (UTC-4) — SP fica em America/Sao_Paulo mesmo sem DST.
    'AM': 'America/Manaus',
    'RR': 'America/Manaus',  # RR historicamente usa o mesmo horário de Manaus
    'RO': 'America/Manaus',
    'MT': 'America/Manaus',
    'MS': 'America/Manaus',

    # America/Noronha (UTC-2) — arquipélago de Fernando de Noronha (PE).
    # Mantido separado do restante de PE só onde aplicável (ver observação
    # abaixo — resolução é por UF, não por município específico).
    # Não há UF própria para Noronha, então não entra neste dict por UF;
    # ver NORONHA_IBGE_CODES abaixo para o caso especial.

    # America/Sao_Paulo (UTC-3) — restante do país (horário de Brasília).
    'AP': 'America/Sao_Paulo',
    'PA': 'America/Sao_Paulo',
    'TO': 'America/Sao_Paulo',
    'MA': 'America/Sao_Paulo',
    'PI': 'America/Sao_Paulo',
    'CE': 'America/Sao_Paulo',
    'RN': 'America/Sao_Paulo',
    'PB': 'America/Sao_Paulo',
    'PE': 'America/Sao_Paulo',
    'AL': 'America/Sao_Paulo',
    'SE': 'America/Sao_Paulo',
    'BA': 'America/Sao_Paulo',
    'MG': 'America/Sao_Paulo',
    'ES': 'America/Sao_Paulo',
    'RJ': 'America/Sao_Paulo',
    'SP': 'America/Sao_Paulo',
    'PR': 'America/Sao_Paulo',
    'SC': 'America/Sao_Paulo',
    'RS': 'America/Sao_Paulo',
    'GO': 'America/Sao_Paulo',
    'DF': 'America/Sao_Paulo',
}

# Exceção pontual: o arquipélago de Fernando de Noronha (código IBGE
# 2605459, UF=PE) usa America/Noronha, diferente do restante de
# Pernambuco. É o único caso no Brasil em que o fuso não é 100% resolvido
# pela UF — por isso este mapeamento por código IBGE tem prioridade sobre
# `TIMEZONE_BY_UF` na resolução (ver `resolve_timezone`).
TIMEZONE_BY_IBGE_OVERRIDE = {
    '2605459': 'America/Noronha',  # Fernando de Noronha/PE
}


def resolve_timezone(uf: str, codigo_ibge: str | None = None) -> str | None:
    """
    Resolve o timezone IANA de um município a partir da UF, com override
    pontual por código IBGE para o caso de Fernando de Noronha.

    Retorna None se a UF não for reconhecida.
    """
    if codigo_ibge and codigo_ibge in TIMEZONE_BY_IBGE_OVERRIDE:
        return TIMEZONE_BY_IBGE_OVERRIDE[codigo_ibge]
    return TIMEZONE_BY_UF.get((uf or '').upper())
