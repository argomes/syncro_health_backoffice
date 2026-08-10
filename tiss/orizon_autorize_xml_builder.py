"""
BACFF-014 — Monta o XML TISS para o Autorize da Orizon (operação
solicitacaoProcedimentoWS), DIFERENTE do envelope genérico ANS usado em
xml_builder.py (mensagemTISS/epilogo, para envio de lote via Fature).

Fonte de verdade: `Autorize-Integracao-Tecnica-Webservice-TISS-4-01-00.pdf`
(manual técnico OFICIAL da Orizon, material público, capítulos 1-13 lidos
por completo em 2026-07-17), não o WSDL genérico da ANS.

CORREÇÃO 2026-08-10 — confirmado contra `20260515_Autorize Integração
Tecnica Webservice - TISS 4.03.00.pdf` (manual mais novo, mesma fonte
oficial, capítulos 10 e 12 lidos por completo): TODAS as mensagens do
Autorize (solicitação, cancelamento, status) são enviadas dentro de um
`<soapenv:Envelope><soapenv:Header/><soapenv:Body>...</soapenv:Body>
</soapenv:Envelope>` real — confirmado em 7 exemplos de XML no Cap. 10 e no
screenshot literal de uma chamada real via SoapUI no Cap. 12. A suposição
anterior ("SEM envelope", baseada no manual 4.01.00) nunca foi testada
contra sandbox real (BACFF-014, bloqueado até clínica-piloto credenciada) e
estava incorreta — corrigida agora que há confirmação oficial explícita.
Ver `_wrap_soap_envelope` abaixo, aplicada nas 3 funções `build_*_xml`.

Diferenças estruturais confirmadas contra o manual (ver BACFF-014 em
`.claude/tasks/BACKOFFICE-TASKS-AVULSAS.md` do repo SyncroHealth):
- Wrapper da operação termina em "WS" (solicitacaoProcedimentoWS), não
  mensagemTISS — cabecalho/solicitacaoProcedimento/hash são filhos diretos
  desse wrapper, SEM o `<epilogo>` do padrão ANS (mas COM `soapenv:Envelope`
  por fora, ver correção acima — só o wrapper interno é diferente do Fature,
  não o transporte SOAP).
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


def _wrap_soap_envelope(ws_body: str) -> str:
    """
    Envelopa o corpo (wrapper "WS" completo, ex. <sch:solicitacaoProcedimentoWS>...
    </sch:solicitacaoProcedimentoWS>) num soap:Envelope real — confirmado
    contra os 7 exemplos de XML do Cap. 10 do manual Autorize 4.03.00 (ver
    correção 2026-08-10 no docstring do módulo). Namespaces (soapenv/sch/xd)
    copiados literalmente dos exemplos oficiais.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        f'xmlns:sch="{SCH_NAMESPACE}" xmlns:xd="http://www.w3.org/2000/09/xmldsig#">'
        '<soapenv:Header/>'
        '<soapenv:Body>'
        f'{ws_body}'
        '</soapenv:Body>'
        '</soapenv:Envelope>'
    )


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

    BACFF-014 (P1, 2026-07-30): `numeroGuiaPrincipal`, `codValidacao`,
    `nomeProfissional` (dentro de `profissionalSolicitante`) e `observacao`
    confirmados no exemplo oficial do manual (Cap. 10, mesmo bloco já usado
    para `indicacaoClinica`/`ausenciaCodValidacao`) e adicionados aqui.
    Nenhum é PII de paciente — nome do profissional é dado de negócio da
    clínica, não do beneficiário.
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

    # numeroGuiaPrincipal: usado quando esta solicitação é uma
    # continuação/prorrogação de outra guia já autorizada. Sem esse
    # relacionamento disponível no model hoje, repete o próprio
    # numeroGuiaPrestador (mesmo padrão do exemplo do manual, onde os dois
    # campos aparecem com valores próprios mas sem regra de derivação
    # documentada além de "identificador da guia").
    numero_guia_principal = _esc(getattr(guia, 'numero_guia_principal', '') or guia.numero)
    # codValidacao: código de validação/senha prévia da operadora quando
    # exigido (ex.: Bradesco/tipoEtapaAutorizacao=1). Sem esse dado
    # disponível no model ainda, mantém o comportamento já existente de
    # ausenciaCodValidacao='01' (justificativa de ausência) e envia
    # codValidacao vazio — o manual mostra os dois campos como irmãos no
    # mesmo exemplo, não como mutuamente exclusivos.
    cod_validacao = _esc(getattr(guia, 'cod_validacao', '') or '')
    nome_profissional = _esc(getattr(guia, 'nome_profissional_solicitante', '') or 'Não informado')
    observacao = _esc(getattr(guia, 'observacao', '') or '')

    # Ordem dos elementos segue exatamente a sequência do exemplo oficial do
    # manual (Cap. 10, "XML Solicitação de Solicitação Procedimentos") — o
    # XSD do padrão TISS é sequencial, ordem errada rejeita por schema
    # inválido mesmo com todos os campos presentes. Corrigido aqui também
    # `indicacaoClinica`, que antes desta rodada estava fora de ordem (logo
    # após `ausenciaCodValidacao`, quando o manual mostra depois de
    # `dataSolicitacao`).
    return (
        '<sch:solicitacaoSP-SADT>'
        '<sch:cabecalhoSolicitacao>'
        f'<sch:registroANS>{registro_ans}</sch:registroANS>'
        f'<sch:numeroGuiaPrestador>{numero_guia}</sch:numeroGuiaPrestador>'
        '</sch:cabecalhoSolicitacao>'
        f'<sch:numeroGuiaPrincipal>{numero_guia_principal}</sch:numeroGuiaPrincipal>'
        '<sch:ausenciaCodValidacao>01</sch:ausenciaCodValidacao>'
        f'<sch:codValidacao>{cod_validacao}</sch:codValidacao>'
        '<sch:tipoEtapaAutorizacao>1</sch:tipoEtapaAutorizacao>'
        '<sch:dadosBeneficiario>'
        f'<sch:numeroCarteira>{numero_carteira}</sch:numeroCarteira>'
        '<sch:atendimentoRN>N</sch:atendimentoRN>'
        '</sch:dadosBeneficiario>'
        '<sch:dadosSolicitante>'
        '<sch:contratadoSolicitante><sch:codigoPrestadorNaOperadora>0</sch:codigoPrestadorNaOperadora></sch:contratadoSolicitante>'
        '<sch:nomeContratadoSolicitante>Clinica</sch:nomeContratadoSolicitante>'
        '<sch:profissionalSolicitante>'
        f'<sch:nomeProfissional>{nome_profissional}</sch:nomeProfissional>'
        '<sch:conselhoProfissional>01</sch:conselhoProfissional>'
        '<sch:numeroConselhoProfissional>000000</sch:numeroConselhoProfissional>'
        '<sch:UF>35</sch:UF>'
        '<sch:CBOS>201115</sch:CBOS>'
        '</sch:profissionalSolicitante>'
        '</sch:dadosSolicitante>'
        '<sch:caraterAtendimento>1</sch:caraterAtendimento>'
        f'<sch:dataSolicitacao>{data_solicitacao}</sch:dataSolicitacao>'
        f'<sch:indicacaoClinica>{indicacao_clinica}</sch:indicacaoClinica>'
        '<sch:coberturaEspecial>01</sch:coberturaEspecial>'
        f'{procedimentos_xml}'
        '<sch:dadosExecutante>'
        '<sch:codigonaOperadora>0</sch:codigonaOperadora>'
        '<sch:CNES>0000000</sch:CNES>'
        '</sch:dadosExecutante>'
        f'<sch:observacao>{observacao}</sch:observacao>'
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


def _cancela_guia_cabecalho_xml(operator_config, sequencial_transacao: str) -> str:
    """
    <ans:cabecalho> da operação CANCELA_GUIA — schema confirmado contra o
    exemplo oficial do manual Autorize 4.03.00, seção "b. XML Cancelamento
    de Autorização" (página 30-31 do PDF `20260515_Autorize Integração
    Tecnica Webservice - TISS 4.03.00.pdf`, logo após o exemplo de
    `solicitacaoProcedimentoWS`).

    Diferença confirmada contra `_cabecalho_xml` (SOLICITACAO_PROCEDIMENTOS):
    o exemplo de CANCELA_GUIA no manual NÃO inclui `<ans:loginSenhaPrestador>`
    dentro do cabeçalho — só `identificacaoTransacao`/`origem`/`destino`/
    `Padrao`. Reproduzido fielmente aqui (não presumido) — se a Orizon
    rejeitar por falta de autenticação em homologação real, revisar contra
    suporte técnico antes de adicionar login/senha por conta própria.
    """
    now = datetime.now()
    data_registro = now.strftime('%Y-%m-%d')
    hora_registro = now.strftime('%H:%M:%S')
    registro_ans = _esc(operator_config.registro_ans)

    return (
        '<ans:cabecalho>'
        '<ans:identificacaoTransacao>'
        '<ans:tipoTransacao>CANCELA_GUIA</ans:tipoTransacao>'
        f'<ans:sequencialTransacao>{_esc(sequencial_transacao)}</ans:sequencialTransacao>'
        f'<ans:dataRegistroTransacao>{data_registro}</ans:dataRegistroTransacao>'
        f'<ans:horaRegistroTransacao>{hora_registro}</ans:horaRegistroTransacao>'
        '</ans:identificacaoTransacao>'
        '<ans:origem><ans:identificacaoPrestador>'
        '<ans:codigoPrestadorNaOperadora>0</ans:codigoPrestadorNaOperadora>'
        '</ans:identificacaoPrestador></ans:origem>'
        f'<ans:destino><ans:registroANS>{registro_ans}</ans:registroANS></ans:destino>'
        f'<ans:Padrao>{_esc(get_tiss_padrao_versao_orizon())}</ans:Padrao>'
        '</ans:cabecalho>'
    )


def _status_autorizacao_cabecalho_xml(operator_config, sequencial_transacao: str) -> str:
    """
    <ans:cabecalho> da operação SOLICITACAO_STATUS_AUTORIZACAO (BO-08.5).

    NÃO confirmado contra exemplo oficial de request (o Cap. 10 do manual
    documenta o fluxograma da operação — solicitacaoProcedimento ->
    autorizacaoProcedimento; se "Em Análise" -> solicitacaoStatusAutorizacao
    -> situacaoAutorizacao — não o XML de request). Estrutura assumida por
    analogia com `_cabecalho_xml` (SOLICITACAO_PROCEDIMENTOS): mantém
    `loginSenhaPrestador` porque é uma operação autenticada como as demais do
    Autorize (diferente de CANCELA_GUIA, cujo exemplo oficial confirmou a
    ausência do bloco). Revisar contra homologação real assim que houver
    credenciais (mesmo bloqueio já registrado em BACFF-014).
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
        '<ans:cabecalho>'
        '<ans:identificacaoTransacao>'
        '<ans:tipoTransacao>SOLICITACAO_STATUS_AUTORIZACAO</ans:tipoTransacao>'
        f'<ans:sequencialTransacao>{_esc(sequencial_transacao)}</ans:sequencialTransacao>'
        f'<ans:dataRegistroTransacao>{data_registro}</ans:dataRegistroTransacao>'
        f'<ans:horaRegistroTransacao>{hora_registro}</ans:horaRegistroTransacao>'
        '</ans:identificacaoTransacao>'
        '<ans:origem><ans:identificacaoPrestador>'
        '<ans:codigoPrestadorNaOperadora>0</ans:codigoPrestadorNaOperadora>'
        '</ans:identificacaoPrestador></ans:origem>'
        f'<ans:destino><ans:registroANS>{registro_ans}</ans:registroANS></ans:destino>'
        f'<ans:Padrao>{_esc(get_tiss_padrao_versao_orizon())}</ans:Padrao>'
        '<ans:loginSenhaPrestador>'
        f'<ans:loginPrestador>{login}</ans:loginPrestador>'
        f'<ans:senhaPrestador>{senha_hash}</ans:senhaPrestador>'
        '</ans:loginSenhaPrestador>'
        '</ans:cabecalho>'
    )


def build_status_autorizacao_xml(
    numero_guia_prestador: str, numero_guia_operadora: str,
    clinic, operator_config, sequencial_transacao: str,
) -> tuple[str, str]:
    """
    BO-08.5 — Monta o envelope SOAP completo de `solicitacaoStatusAutorizacaoWS`
    (Autorize Orizon), usado para consultar o status de uma autorização
    devolvida anteriormente como "Em Análise" (`situacaoAutorizacao=2`, ver
    `orizon_autorize_client.py::SituacaoAutorizacao.EM_ANALISE`).

    Wrapper/operação confirmados contra
    `tissSolicitacaoStatusAutorizacaoV4_02_00.wsdl` (mensagem de request:
    `ans:solicitacaoStatusAutorizacaoWS`; resposta: `ans:situacaoAutorizacaoWS`
    — tratada em `orizon_autorize_client._parse_response`, que reaproveita a
    mesma estrutura de `autorizacaoSP-SADT` já usada por
    `autorizacaoProcedimentoWS`). O CORPO interno (quais campos identificam a
    guia a consultar) não está documentado no manual disponível — assumido
    por analogia com `build_cancelamento_guia_xml` (par
    numeroGuiaPrestador/numeroGuiaOperadora, mesmo par que a operação de
    cancelamento usa para identificar a guia). Revisar contra homologação
    real assim que houver credenciais (mesmo bloqueio já registrado em
    BACFF-014 para as demais operações do Autorize).

    `numero_guia_operadora` é opcional (pode ainda não ter sido atribuído
    pela operadora na resposta "Em Análise" original) — omitido do XML
    quando vazio, nunca enviado como tag vazia.

    Retorna (xml, hash_md5).
    """
    if not numero_guia_prestador:
        raise OrizonAutorizeXMLBuilderError('numero_guia_prestador_obrigatorio')

    cabecalho_xml = _status_autorizacao_cabecalho_xml(operator_config, sequencial_transacao)
    numero_guia_prestador_esc = _esc(numero_guia_prestador)
    numero_guia_operadora_xml = (
        f'<ans:numeroGuiaOperadora>{_esc(numero_guia_operadora)}</ans:numeroGuiaOperadora>'
        if numero_guia_operadora else ''
    )

    corpo_sem_hash = (
        f'<ans:solicitacaoStatusAutorizacaoWS xmlns:ans="{SCH_NAMESPACE}">'
        f'{cabecalho_xml}'
        '<ans:solicitacaoStatusAutorizacao>'
        f'<ans:numeroGuiaPrestador>{numero_guia_prestador_esc}</ans:numeroGuiaPrestador>'
        f'{numero_guia_operadora_xml}'
        '</ans:solicitacaoStatusAutorizacao>'
    )

    # Mesma regra de hash já aplicada nas demais operações do Autorize (ver
    # nota em `build_cancelamento_guia_xml`).
    hash_md5 = hashlib.md5(corpo_sem_hash.encode('utf-8')).hexdigest()

    ws_body = (
        f'{corpo_sem_hash}'
        f'<ans:hash>{hash_md5}</ans:hash>'
        '</ans:solicitacaoStatusAutorizacaoWS>'
    )
    xml_completo = _wrap_soap_envelope(ws_body)
    return xml_completo, hash_md5


def build_cancelamento_guia_xml(guia, clinic, operator_config, sequencial_transacao: str) -> tuple[str, str]:
    """
    Monta o envelope SOAP completo de `cancelaGuiaWS` (Autorize Orizon) para
    cancelar UMA guia já autorizada previamente.

    Schema confirmado (não adivinhado) contra o exemplo oficial do manual
    Autorize 4.03.00, seção "b. XML Cancelamento de Autorização" (página
    30-31 do PDF `20260515_Autorize Integração Tecnica Webservice - TISS
    4.03.00.pdf`) — mesmo capítulo que documenta `solicitacaoProcedimentoWS`
    (item "a."), logo antes de `comunicacaoBeneficiarioWS` (item "c.").

    Estrutura confirmada: wrapper raiz `<ans:cancelaGuiaWS>` com
    `cabecalho`/`cancelaGuia`/`hash` como filhos diretos (mesmo padrão "WS"
    sem `<mensagemTISS>`/`<epilogo>` já usado em `solicitacaoProcedimentoWS`).
    Dentro de `<ans:cancelaGuia>`: `<ans:dadosPrestador><ans:
    codigoPrestadorNaOperadora>` e `<ans:tipoCancelamento><ans:
    tipoCancelamentoGuia><ans:tipoGuia>`/`<ans:numeroGuiaPrestador>`. O
    manual confirma explicitamente: "No cancelamento da Guia o campo Nº
    Guia Operadora deve ser preenchido com o conteúdo da mensagem de
    retorno da Solicitação de SP-SADT" — mas o próprio exemplo XML usa
    `numeroGuiaPrestador` (não `numeroGuiaOperadora`) dentro de
    `tipoCancelamentoGuia`; seguido aqui o exemplo XML literal (fonte
    primária), não a prosa descritiva.

    `tipoGuia=1` — SP-SADT (mesmo tipo de guia usado em
    `build_solicitacao_procedimento_xml`; o manual não documenta a tabela
    completa de `tipoGuia` neste capítulo, valor do próprio exemplo oficial).

    Retorna (xml, hash_md5).
    """
    if guia is None:
        raise OrizonAutorizeXMLBuilderError('guia_obrigatoria')

    cabecalho_xml = _cancela_guia_cabecalho_xml(operator_config, sequencial_transacao)
    numero_guia = _esc(guia.numero)

    corpo_sem_hash = (
        f'<ans:cancelaGuiaWS xmlns:ans="{SCH_NAMESPACE}">'
        f'{cabecalho_xml}'
        '<ans:cancelaGuia>'
        '<ans:dadosPrestador><ans:codigoPrestadorNaOperadora>0</ans:codigoPrestadorNaOperadora></ans:dadosPrestador>'
        '<ans:tipoCancelamento><ans:tipoCancelamentoGuia>'
        '<ans:tipoGuia>1</ans:tipoGuia>'
        f'<ans:numeroGuiaPrestador>{numero_guia}</ans:numeroGuiaPrestador>'
        '</ans:tipoCancelamentoGuia></ans:tipoCancelamento>'
        '</ans:cancelaGuia>'
    )

    # Mesma regra de hash já aplicada em build_solicitacao_procedimento_xml
    # (assumida por analogia — o manual não detalha a regra de hash para
    # nenhuma operação do Autorize, só mostra `<ans:hash>?</ans:hash>` como
    # placeholder no exemplo).
    hash_md5 = hashlib.md5(corpo_sem_hash.encode('utf-8')).hexdigest()

    ws_body = (
        f'{corpo_sem_hash}'
        f'<ans:hash>{hash_md5}</ans:hash>'
        '</ans:cancelaGuiaWS>'
    )
    xml_completo = _wrap_soap_envelope(ws_body)
    return xml_completo, hash_md5


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

    ws_body = (
        f'{corpo_sem_hash}'
        f'<sch:hash>{hash_md5}</sch:hash>'
        '</sch:solicitacaoProcedimentoWS>'
    )
    xml_completo = _wrap_soap_envelope(ws_body)
    return xml_completo, hash_md5
