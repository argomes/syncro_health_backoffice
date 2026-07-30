"""
TASK-BO-10 — Monta o fragmento XML de `envioDocumentoWS` (Orizon), a ÚNICA
operação Orizon que exige assinatura XMLDSig (envio de anexo/imagem de uma
guia já registrada). Diferente de `loteGuiasWS` (faturamento em lote) e da
autorização (`orizon_autorize_xml_builder.py`), que não exigem assinatura —
este módulo NÃO toca nesses fluxos.

Segue o mesmo padrão de string-building de `orizon_autorize_xml_builder.py`
(campos mínimos confirmados por analogia estrutural com o manual técnico
Orizon, Cap. 10 — não homologado contra sandbox real, ver BACFF-014/TASK-BO-10
no tracker), mas com uma etapa extra: canonicalização C14N (XML Exclusive
Canonicalization não é necessária aqui — C14N "clássico", que é o que
`lxml.etree.tostring(method='c14n')` produz, e é suficiente porque o
assinante real, do lado do gateway Go, decide o algoritmo de canonicalização
de fato usado no <SignedInfo>; este lado só precisa fixar bytes estáveis e
determinísticos para o fragmento que vai ser assinado).

Escolha de biblioteca para C14N (documentada aqui e no PR): `lxml` PURO —
já é dependência do repo (`soap_client.py`, `xml_builder.py`) e suporta C14N
nativamente via `etree.tostring(el, method="c14n")`, sem exigir
`libxmlsec1` no sistema operacional (facilita build em Railway/Docker,
diferente de `xmlsec`). A ASSINATURA de verdade nunca acontece neste lado —
só no gateway Go, que tem o `.p12` — então não precisamos de `signxml` nem de
`xmlsec` aqui: só canonicalização, que o lxml já cobre.
"""
from xml.sax.saxutils import escape

from lxml import etree

SCH_NAMESPACE = 'http://www.ans.gov.br/padroes/tiss/schemas'
ROOT_TAG = 'sch:envioDocumentoWS'


class OrizonEnvioDocumentoXMLBuilderError(Exception):
    pass


def _esc(value) -> str:
    return escape(str(value if value is not None else ''))


def _cabecalho_xml(clinic, operator_config, sequencial_transacao: str) -> str:
    """
    <sch:cabecalho> com autenticação login/senha (mesmo padrão de
    `orizon_autorize_xml_builder._cabecalho_xml`) — reaproveitado aqui por
    analogia estrutural, já que o manual não detalha um cabeçalho diferente
    para envioDocumentoWS.
    """
    import hashlib
    from datetime import datetime

    now = datetime.now()
    data_registro = now.strftime('%Y-%m-%d')
    hora_registro = now.strftime('%H:%M:%S')
    registro_ans = _esc(operator_config.registro_ans)

    login_plain = operator_config.connection.login_plain
    senha_plain = operator_config.connection.senha_plain
    if not login_plain or not senha_plain:
        raise OrizonEnvioDocumentoXMLBuilderError('operator_config_sem_login_senha')

    login = _esc(login_plain)
    senha_hash = _esc(hashlib.md5(senha_plain.encode('utf-8')).hexdigest())

    return (
        '<sch:cabecalho>'
        '<sch:identificacaoTransacao>'
        '<sch:tipoTransacao>ENVIO_DOCUMENTO</sch:tipoTransacao>'
        f'<sch:sequencialTransacao>{_esc(sequencial_transacao)}</sch:sequencialTransacao>'
        f'<sch:dataRegistroTransacao>{data_registro}</sch:dataRegistroTransacao>'
        f'<sch:horaRegistroTransacao>{hora_registro}</sch:horaRegistroTransacao>'
        '</sch:identificacaoTransacao>'
        '<sch:origem><sch:identificacaoPrestador>'
        '<sch:codigoPrestadorNaOperadora>0</sch:codigoPrestadorNaOperadora>'
        '</sch:identificacaoPrestador></sch:origem>'
        f'<sch:destino><sch:registroANS>{registro_ans}</sch:registroANS></sch:destino>'
        '<sch:loginSenhaPrestador>'
        f'<sch:loginPrestador>{login}</sch:loginPrestador>'
        f'<sch:senhaPrestador>{senha_hash}</sch:senhaPrestador>'
        '</sch:loginSenhaPrestador>'
        '</sch:cabecalho>'
    )


def _documento_xml(guia, documento_base64: str, nome_arquivo: str, tipo_documento: str) -> str:
    """
    <sch:documento> — anexo/imagem referente à guia já registrada
    (`numeroGuiaPrestador`), conteúdo em base64 (a codificação, não a
    assinatura — texto puro, sem PHI adicional além do que já está na guia).
    """
    numero_guia = _esc(guia.numero)
    return (
        '<sch:documento>'
        f'<sch:numeroGuiaPrestador>{numero_guia}</sch:numeroGuiaPrestador>'
        f'<sch:tipoDocumento>{_esc(tipo_documento)}</sch:tipoDocumento>'
        f'<sch:nomeArquivo>{_esc(nome_arquivo)}</sch:nomeArquivo>'
        f'<sch:conteudoBase64>{_esc(documento_base64)}</sch:conteudoBase64>'
        '</sch:documento>'
    )


def build_envio_documento_fragment(
    *, guia, clinic, operator_config, sequencial_transacao: str,
    documento_base64: str, nome_arquivo: str, tipo_documento: str = 'ANEXO',
) -> tuple[str, str]:
    """
    Monta o fragmento `envioDocumentoWS` (SEM `<Signature>`) e o canonicaliza
    (C14N) — é este resultado, e SÓ ele, que fica armazenado em
    `TISSDocumentoAssinatura.fragmento_canonico` e que deve ser assinado pelo
    gateway. Nunca reparsear/re-serializar depois.

    Retorna (fragmento_canonico: str, root_tag: str). `root_tag` é o nome
    qualificado da tag raiz (`sch:envioDocumentoWS`), usado só para localizar
    o ponto de inserção textual do bloco de assinatura depois — não para
    reparsear o documento.
    """
    if guia is None:
        raise OrizonEnvioDocumentoXMLBuilderError('guia_obrigatoria')
    if not documento_base64:
        raise OrizonEnvioDocumentoXMLBuilderError('documento_base64_obrigatorio')

    cabecalho_xml = _cabecalho_xml(clinic, operator_config, sequencial_transacao)
    documento_xml = _documento_xml(guia, documento_base64, nome_arquivo, tipo_documento)

    # Documento intermediário — bem-formado, mas ainda não canônico (ordem
    # de atributos/whitespace podem variar). Serializado com string
    # concatenada (mesmo padrão dos outros builders do repo) e então
    # reparseado UMA VEZ, só para poder canonicalizar via lxml — este é o
    # único parse/serialize desta função; o resultado abaixo (fragmento
    # canônico) nunca mais passa por isso.
    xml_bem_formado = (
        f'<{ROOT_TAG} xmlns:sch="{SCH_NAMESPACE}">'
        f'{cabecalho_xml}'
        f'{documento_xml}'
        f'</{ROOT_TAG}>'
    )

    try:
        root_element = etree.fromstring(xml_bem_formado.encode('utf-8'))
    except etree.XMLSyntaxError as exc:
        raise OrizonEnvioDocumentoXMLBuilderError('fragmento_malformado') from exc

    fragmento_canonico_bytes = etree.tostring(root_element, method='c14n')
    fragmento_canonico = fragmento_canonico_bytes.decode('utf-8')

    return fragmento_canonico, ROOT_TAG
