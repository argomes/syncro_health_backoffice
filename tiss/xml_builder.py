"""
BO-08.3 — Monta o XML TISS (mensagemTISS) para envio de lote de guias
(operação tissEnvioDocumentos_Operation / prestadorParaOperadora > loteGuias).

Fonte de verdade: XSDs oficiais ANS em
`Documents/PadroTISSComunicao202505/Padrão TISS Comunicação 040200/`
(tissV4_02_00.xsd, tissComplexTypesV4_02_00.xsd, tissGuiasV4_02_00.xsd).

Estrutura: mensagemTISS > cabecalho (cabecalhoTransacao) +
prestadorParaOperadora > loteGuias (ctm_guiaLote, uma guiaSP-SADT por
TISSGuia) + epilogo (hash MD5).

Versão do Padrão: o XSD baixado (tissV4_02_00.xsd) só enumera `dm_versao`
até "4.02.00" — a spec do produto menciona "TISS v4.03.00", mas essa versão
ainda não existe no XSD oficial disponível localmente. Usamos "4.02.00" para
que o XML gerado valide contra o XSD real (ver xml_validator.py); quando o
XSD 4.03.00 for obtido, só é necessário atualizar TISS_PADRAO_VERSAO abaixo.

Hash do epílogo: calculado sobre o XML SEM os elementos <hash> e <Signature>
— por isso build_lote_xml monta o XML em duas passadas: primeiro sem
epilogo/hash, calcula o MD5 do que já existe (cabecalho + prestadorOperadora),
depois insere <epilogo><hash>...</hash></epilogo>.
"""
import hashlib
import re
from datetime import datetime
from xml.sax.saxutils import escape

TISS_PADRAO_VERSAO = '4.02.00'
TISS_NAMESPACE = 'http://www.ans.gov.br/padroes/tiss/schemas'


class XMLBuilderError(Exception):
    pass


def _esc(value) -> str:
    return escape(str(value if value is not None else ''))


def _procedimento_xml(proc: dict, sequencial: int) -> str:
    """
    Um <procedimentoExecutado> (ct_procedimentoExecutadoSadt). Campos
    obrigatórios: sequencialItem, dataExecucao, procedimento (codigoTabela +
    codigoProcedimento + descricaoProcedimento), quantidadeExecutada,
    reducaoAcrescimo, valorUnitario, valorTotal.
    """
    codigo_tabela = _esc(proc.get('codigo_tabela', '22'))  # 22 = TUSS
    codigo_proc = _esc(proc.get('codigo', '00000000'))
    descricao = _esc(proc.get('descricao', 'Procedimento'))
    qtd = _esc(proc.get('quantidade', 1))
    valor_unitario = _esc(f"{float(proc.get('valor_unitario', proc.get('valor', 0))):.2f}")
    valor_total = _esc(f"{float(proc.get('valor_total', proc.get('valor', 0))):.2f}")
    data_execucao = _esc(proc.get('data_execucao', datetime.now().strftime('%Y-%m-%d')))
    return (
        '<procedimentoExecutado>'
        f'<sequencialItem>{sequencial:04d}</sequencialItem>'
        f'<dataExecucao>{data_execucao}</dataExecucao>'
        '<procedimento>'
        f'<codigoTabela>{codigo_tabela}</codigoTabela>'
        f'<codigoProcedimento>{codigo_proc}</codigoProcedimento>'
        f'<descricaoProcedimento>{descricao}</descricaoProcedimento>'
        '</procedimento>'
        f'<quantidadeExecutada>{qtd}</quantidadeExecutada>'
        '<reducaoAcrescimo>0.00</reducaoAcrescimo>'
        f'<valorUnitario>{valor_unitario}</valorUnitario>'
        f'<valorTotal>{valor_total}</valorTotal>'
        '</procedimentoExecutado>'
    )


def _guia_sp_sadt_xml(guia, operator_config) -> str:
    """
    <guiaSP-SADT> (ctm_sp-sadtGuia) mínima e estruturalmente válida contra o
    XSD oficial (ver comentário de módulo — campos opcionais omitidos).
    """
    procedimentos = guia.procedimentos or []
    procedimentos_xml = ''.join(
        _procedimento_xml(p, i + 1) for i, p in enumerate(procedimentos)
    ) or _procedimento_xml({}, 1)

    valor_total = f'{float(guia.valor or 0):.2f}'
    numero_carteira = _esc(guia.numero_carteira or '0')
    numero_guia = _esc(guia.numero)
    registro_ans = _esc(operator_config.registro_ans)

    return (
        '<guiaSP-SADT>'
        '<cabecalhoGuia>'
        f'<registroANS>{registro_ans}</registroANS>'
        f'<numeroGuiaPrestador>{numero_guia}</numeroGuiaPrestador>'
        '</cabecalhoGuia>'
        '<dadosBeneficiario>'
        f'<numeroCarteira>{numero_carteira}</numeroCarteira>'
        '<atendimentoRN>N</atendimentoRN>'
        '</dadosBeneficiario>'
        '<dadosSolicitante>'
        '<contratadoSolicitante><codigoPrestadorNaOperadora>0</codigoPrestadorNaOperadora></contratadoSolicitante>'
        '<nomeContratadoSolicitante>Clinica</nomeContratadoSolicitante>'
        '<profissionalSolicitante>'
        '<conselhoProfissional>01</conselhoProfissional>'
        '<numeroConselhoProfissional>000000</numeroConselhoProfissional>'
        '<UF>35</UF>'
        '<CBOS>201115</CBOS>'
        '</profissionalSolicitante>'
        '</dadosSolicitante>'
        '<dadosSolicitacao>'
        '<caraterAtendimento>1</caraterAtendimento>'
        '</dadosSolicitacao>'
        '<dadosExecutante>'
        '<contratadoExecutante><codigoPrestadorNaOperadora>0</codigoPrestadorNaOperadora></contratadoExecutante>'
        '<CNES>0000000</CNES>'
        '</dadosExecutante>'
        '<dadosAtendimento>'
        '<tipoAtendimento>01</tipoAtendimento>'
        '<indicacaoAcidente>9</indicacaoAcidente>'
        '<regimeAtendimento>01</regimeAtendimento>'
        '</dadosAtendimento>'
        '<procedimentosExecutados>'
        f'{procedimentos_xml}'
        '</procedimentosExecutados>'
        '<valorTotal>'
        f'<valorTotalGeral>{valor_total}</valorTotalGeral>'
        '</valorTotal>'
        '</guiaSP-SADT>'
    )


def _cabecalho_transacao_xml(clinic, operator_config, sequencial_transacao: str) -> str:
    now = datetime.now()
    data_registro = now.strftime('%Y-%m-%d')
    hora_registro = now.strftime('%H:%M:%S')
    registro_ans = _esc(operator_config.registro_ans)
    cnpj_digits = re.sub(r'\D', '', clinic.cnpj or '') or '00000000000000'
    cnpj_prestador = _esc(cnpj_digits[:14].ljust(14, '0'))
    return (
        '<cabecalho>'
        '<identificacaoTransacao>'
        '<tipoTransacao>ENVIO_LOTE_GUIAS</tipoTransacao>'
        f'<sequencialTransacao>{_esc(sequencial_transacao)}</sequencialTransacao>'
        f'<dataRegistroTransacao>{data_registro}</dataRegistroTransacao>'
        f'<horaRegistroTransacao>{hora_registro}</horaRegistroTransacao>'
        '</identificacaoTransacao>'
        '<origem><identificacaoPrestador><CNPJ>' + cnpj_prestador + '</CNPJ></identificacaoPrestador></origem>'
        f'<destino><registroANS>{registro_ans}</registroANS></destino>'
        f'<Padrao>{TISS_PADRAO_VERSAO}</Padrao>'
        '</cabecalho>'
    )


def build_lote_xml(lote, guias: list, clinic, operator_config, sequencial_transacao: str) -> str:
    """
    Monta o XML completo do mensagemTISS para o lote, incluindo o epílogo com
    hash MD5. Retorna a string XML (UTF-8, declarada como tal no prólogo —
    Orizon aceita UTF-8 mesmo que o WSDL declare ISO-8859-1, conforme
    documentado na spec do produto).
    """
    if not guias:
        raise XMLBuilderError('lote_sem_guias')

    numero_lote = _esc(f'{lote.numero_lote:012d}')
    cabecalho_xml = _cabecalho_transacao_xml(clinic, operator_config, sequencial_transacao)
    guias_xml = ''.join(_guia_sp_sadt_xml(g, operator_config) for g in guias)

    corpo_sem_epilogo = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<mensagemTISS xmlns="{TISS_NAMESPACE}">'
        f'{cabecalho_xml}'
        '<prestadorParaOperadora>'
        '<loteGuias>'
        f'<numeroLote>{numero_lote}</numeroLote>'
        f'<guiasTISS>{guias_xml}</guiasTISS>'
        '</loteGuias>'
        '</prestadorParaOperadora>'
    )

    # Hash MD5 calculado sobre o XML sem <hash> e sem <Signature> — nesta
    # etapa, `corpo_sem_epilogo` ainda não tem nenhum dos dois, então o hash
    # é calculado direto sobre ele + a tag de fechamento (sem o epilogo).
    hash_md5 = hashlib.md5(corpo_sem_epilogo.encode('utf-8')).hexdigest()

    xml_completo = (
        f'{corpo_sem_epilogo}'
        f'<epilogo><hash>{hash_md5}</hash></epilogo>'
        '</mensagemTISS>'
    )
    return xml_completo, hash_md5
