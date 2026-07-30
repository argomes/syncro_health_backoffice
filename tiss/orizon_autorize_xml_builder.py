"""
BACFF-014 — Monta o XML TISS para o Autorize da Orizon (operação
solicitacaoProcedimentoWS), DIFERENTE do envelope genérico ANS usado em
xml_builder.py (mensagemTISS/epilogo, para envio de lote via Fature).

Fonte de verdade: `Autorize-Integracao-Tecnica-Webservice-TISS-4-01-00.pdf`
(manual técnico OFICIAL da Orizon, material público, capítulos 1-13 lidos
por completo em 2026-07-17), não o WSDL genérico da ANS.

Diferenças estruturais confirmadas contra o manual (ver BACFF-014 em
`.claude/tasks/BACKOFFICE-TASKS-AVULSAS.md` do repo SyncroHealth):
- Wrapper da operação termina em "WS" (solicitacaoProcedimentoWS), não
  mensagemTISS — cabecalho/solicitacaoProcedimento/hash são filhos diretos
  desse wrapper, SEM o envelope <mensagemTISS>/<epilogo> do padrão ANS.
- Autenticação obrigatória dentro do <cabecalho>: <loginSenhaPrestador>
  com <loginPrestador> e <senhaPrestador> (senha em hash MD5) — OU
  certificado digital (não implementado aqui; ver EDGW-041, certificado é
  OPCIONAL na Orizon, não mTLS obrigatório).
- Não existe operação de elegibilidade isolada: a elegibilidade do
  beneficiário é verificada DENTRO do próprio pedido de autorização
  (solicitacaoSP-SADT), resposta pode vir imediata ou "Em Análise"
  (consultada depois via solicitacaoStatusAutorizacao — não implementado
  neste módulo ainda).
- Versão do padrão é parametrizável via `settings.TISS_PADRAO_VERSAO_ORIZON`
  (default '4.03.00' — manual confirma que o WS aceita 4.01.00/4.02.00/
  4.03.00). Não é mais fixa em código (ver BACFF-014, achado 1 da
  atualização 2026-07-29: estava hardcoded em '4.01.00').

Hash MD5 do epílogo (`<sch:hash>`): mesma regra do padrão ANS — calculado
sobre o XML sem os próprios elementos <hash>/<Signature>.

NÃO validado contra sandbox real da Orizon (bloqueado — precisa de clínica-
cliente credenciada, ver BACFF-014). Construído estritamente contra a
especificação do manual oficial; testes unitários validam estrutura, não
comportamento de operadora real.
"""
import hashlib
from datetime import datetime
from xml.sax.saxutils import escape

from django.conf import settings

SCH_NAMESPACE = 'http://www.ans.gov.br/padroes/tiss/schemas'


def get_tiss_padrao_versao_orizon() -> str:
    """
    Versão do padrão TISS usada nas chamadas ao Autorize da Orizon —
    parametrizável via `settings.TISS_PADRAO_VERSAO_ORIZON` (BACFF-014,
    achado 1 da atualização 2026-07-29: antes fixa em '4.01.00' no código).
    """
    return getattr(settings, 'TISS_PADRAO_VERSAO_ORIZON', '4.03.00')


class OrizonAutorizeXMLBuilderError(Exception):
    pass


def _esc(value) -> str:
    return escape(str(value if value is not None else ''))


def _senha_md5(senha_plain: str) -> str:
    """Senha do prestador vai em hash MD5 dentro de <senhaPrestador> — nunca texto plano."""
    return hashlib.md5(senha_plain.encode('utf-8')).hexdigest()


def _procedimento_solicitado_xml(proc: dict) -> str:
    """
    <procedimentosSolicitados><procedimento>...</procedimento>
    <quantidadeSolicitada>...</quantidadeSolicitada></procedimentosSolicitados>
    — estrutura mínima confirmada contra o exemplo do manual (Cap. 10).
    """
    codigo_tabela = _esc(proc.get('codigo_tabela', '22'))  # 22 = TUSS
    codigo_proc = _esc(proc.get('codigo', '00000000'))
    descricao = _esc(proc.get('descricao', 'Procedimento'))
    qtd = _esc(proc.get('quantidade', 1))
    return (
        '<sch:procedimentosSolicitados>'
        '<sch:procedimento>'
        f'<sch:codigoTabela>{codigo_tabela}</sch:codigoTabela>'
        f'<sch:codigoProcedimento>{codigo_proc}</sch:codigoProcedimento>'
        f'<sch:descricaoProcedimento>{descricao}</sch:descricaoProcedimento>'
        '</sch:procedimento>'
        f'<sch:quantidadeSolicitada>{qtd}</sch:quantidadeSolicitada>'
        '</sch:procedimentosSolicitados>'
    )


def _solicitacao_sp_sadt_xml(guia, operator_config) -> str:
    """
    <sch:solicitacaoSP-SADT> — campos obrigatórios mínimos confirmados
    contra o exemplo XML do manual (Cap. 10, "XML Solicitação de
    Solicitação Procedimentos"). Particularidades por operadora (Bradesco,
    Cabesp, Cassi, Economus, Careplus, Seguros Unimed — ver Cap. 8 do
    manual) NÃO estão implementadas aqui ainda; ficam para quando a
    primeira clínica-piloto usar uma dessas operadoras especificamente
    (ver BACFF-014, "próxima ação executável", item 5).
    """
    procedimentos = guia.procedimentos or []
    procedimentos_xml = ''.join(
        _procedimento_solicitado_xml(p) for p in procedimentos
    ) or _procedimento_solicitado_xml({})

    registro_ans = _esc(operator_config.registro_ans)
    numero_guia = _esc(guia.numero)
    numero_carteira = _esc(guia.numero_carteira or '0')
    data_solicitacao = datetime.now().strftime('%Y-%m-%d')
    # BACFF-014 (achado 2, 2026-07-29): obrigatório pelo manual Cap. 10.
    # Preenchido a partir do CID/indicação clínica registrado na guia; sem
    # dado disponível, usa um placeholder textual explícito (nunca vazio —
    # elemento obrigatório pelo schema) em vez de inventar um CID.
    indicacao_clinica = _esc(getattr(guia, 'indicacao_clinica', '') or 'Não informado')

    return (
        '<sch:solicitacaoSP-SADT>'
        '<sch:cabecalhoSolicitacao>'
        f'<sch:registroANS>{registro_ans}</sch:registroANS>'
        f'<sch:numeroGuiaPrestador>{numero_guia}</sch:numeroGuiaPrestador>'
        '</sch:cabecalhoSolicitacao>'
        '<sch:ausenciaCodValidacao>01</sch:ausenciaCodValidacao>'
        f'<sch:indicacaoClinica>{indicacao_clinica}</sch:indicacaoClinica>'
        '<sch:tipoEtapaAutorizacao>1</sch:tipoEtapaAutorizacao>'
        '<sch:dadosBeneficiario>'
        f'<sch:numeroCarteira>{numero_carteira}</sch:numeroCarteira>'
        '<sch:atendimentoRN>N</sch:atendimentoRN>'
        '</sch:dadosBeneficiario>'
        '<sch:dadosSolicitante>'
        '<sch:contratadoSolicitante><sch:codigoPrestadorNaOperadora>0</sch:codigoPrestadorNaOperadora></sch:contratadoSolicitante>'
        '<sch:nomeContratadoSolicitante>Clinica</sch:nomeContratadoSolicitante>'
        '<sch:profissionalSolicitante>'
        '<sch:conselhoProfissional>01</sch:conselhoProfissional>'
        '<sch:numeroConselhoProfissional>000000</sch:numeroConselhoProfissional>'
        '<sch:UF>35</sch:UF>'
        '<sch:CBOS>201115</sch:CBOS>'
        '</sch:profissionalSolicitante>'
        '</sch:dadosSolicitante>'
        '<sch:caraterAtendimento>1</sch:caraterAtendimento>'
        f'<sch:dataSolicitacao>{data_solicitacao}</sch:dataSolicitacao>'
        '<sch:coberturaEspecial>01</sch:coberturaEspecial>'
        f'{procedimentos_xml}'
        '<sch:dadosExecutante>'
        '<sch:codigonaOperadora>0</sch:codigonaOperadora>'
        '<sch:CNES>0000000</sch:CNES>'
        '</sch:dadosExecutante>'
        '</sch:solicitacaoSP-SADT>'
    )


def _cabecalho_xml(clinic, operator_config, sequencial_transacao: str) -> str:
    """
    <sch:cabecalho> com <sch:loginSenhaPrestador> — autenticação obrigatória
    do Autorize (login/senha MD5, cadastrado self-service no Portal Orizon;
    ver BACFF-014). Certificado digital como alternativa NÃO implementado
    aqui (fica para quando/se uma clínica-piloto precisar).
    """
    now = datetime.now()
    data_registro = now.strftime('%Y-%m-%d')
    hora_registro = now.strftime('%H:%M:%S')
    registro_ans = _esc(operator_config.registro_ans)

    login_plain = operator_config.connection.login_plain
    senha_plain = operator_config.connection.senha_plain
    if not login_plain or not senha_plain:
        raise OrizonAutorizeXMLBuilderError('operator_config_sem_login_senha')

    login = _esc(login_plain)
    senha_hash = _esc(_senha_md5(senha_plain))

    return (
        '<sch:cabecalho>'
        '<sch:identificacaoTransacao>'
        '<sch:tipoTransacao>SOLICITACAO_PROCEDIMENTOS</sch:tipoTransacao>'
        f'<sch:sequencialTransacao>{_esc(sequencial_transacao)}</sch:sequencialTransacao>'
        f'<sch:dataRegistroTransacao>{data_registro}</sch:dataRegistroTransacao>'
        f'<sch:horaRegistroTransacao>{hora_registro}</sch:horaRegistroTransacao>'
        '</sch:identificacaoTransacao>'
        '<sch:origem><sch:identificacaoPrestador>'
        '<sch:codigoPrestadorNaOperadora>0</sch:codigoPrestadorNaOperadora>'
        '</sch:identificacaoPrestador></sch:origem>'
        f'<sch:destino><sch:registroANS>{registro_ans}</sch:registroANS></sch:destino>'
        f'<sch:Padrao>{_esc(get_tiss_padrao_versao_orizon())}</sch:Padrao>'
        '<sch:loginSenhaPrestador>'
        f'<sch:loginPrestador>{login}</sch:loginPrestador>'
        f'<sch:senhaPrestador>{senha_hash}</sch:senhaPrestador>'
        '</sch:loginSenhaPrestador>'
        '</sch:cabecalho>'
    )


def build_solicitacao_procedimento_xml(guia, clinic, operator_config, sequencial_transacao: str) -> tuple[str, str]:
    """
    Monta o envelope SOAP completo de solicitacaoProcedimentoWS (Autorize
    Orizon) para UMA guia SP-SADT. Retorna (xml, hash_md5).

    Diferente de build_lote_xml (xml_builder.py): não é <mensagemTISS>, é o
    wrapper "WS" direto (cabecalho/solicitacaoProcedimento/hash como
    irmãos), sem <epilogo> — o manual mostra <sch:hash>?</sch:hash> como
    filho direto do wrapper raiz.
    """
    if guia is None:
        raise OrizonAutorizeXMLBuilderError('guia_obrigatoria')

    cabecalho_xml = _cabecalho_xml(clinic, operator_config, sequencial_transacao)
    solicitacao_xml = _solicitacao_sp_sadt_xml(guia, operator_config)

    corpo_sem_hash = (
        f'<sch:solicitacaoProcedimentoWS xmlns:sch="{SCH_NAMESPACE}">'
        f'{cabecalho_xml}'
        f'<sch:solicitacaoProcedimento>{solicitacao_xml}</sch:solicitacaoProcedimento>'
    )

    # Hash calculado sobre o XML sem <hash>/<Signature> — mesma regra do
    # padrão ANS (ver xml_builder.py), reaplicada aqui por consistência
    # ainda que o manual da Orizon não detalhe explicitamente essa regra
    # para o Autorize (assumido por analogia com o Fature/padrão ANS geral;
    # revisar se a Orizon rejeitar por hash incorreto em homologação real).
    hash_md5 = hashlib.md5(corpo_sem_hash.encode('utf-8')).hexdigest()

    xml_completo = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'{corpo_sem_hash}'
        f'<sch:hash>{hash_md5}</sch:hash>'
        '</sch:solicitacaoProcedimentoWS>'
    )
    return xml_completo, hash_md5
