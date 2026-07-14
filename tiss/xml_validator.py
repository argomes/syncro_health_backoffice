"""
BO-08.3 — Valida o XML de um lote TISS contra os XSDs oficiais da ANS ANTES
do envio SOAP, para pegar erro estrutural localmente (evitando gastar uma
tentativa de envio real / reduzir glosa por XML malformado).

Usa lxml.etree.XMLSchema sobre tissV4_02_00.xsd (que via <include> já traz
tissSimpleTypesV4_02_00.xsd, tissComplexTypesV4_02_00.xsd e
tissGuiasV4_02_00.xsd) — é o schema do elemento raiz `mensagemTISS`.
"""
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from lxml import etree


class XMLValidatorError(Exception):
    """XSD não encontrado / não pôde ser carregado — erro de configuração, não de XML de negócio."""


@dataclass
class ValidationIssue:
    line: int
    column: int
    message: str

    def __str__(self):
        return f'linha {self.line}, coluna {self.column}: {self.message}'


_schema_cache = None


def _xsd_dir() -> Path:
    xsd_dir = getattr(settings, 'TISS_XSD_DIR', None)
    if not xsd_dir:
        raise XMLValidatorError('TISS_XSD_DIR não configurado')
    return Path(xsd_dir)


def _load_schema() -> etree.XMLSchema:
    global _schema_cache
    if _schema_cache is not None:
        return _schema_cache

    xsd_path = _xsd_dir() / 'tissV4_02_00.xsd'
    if not xsd_path.exists():
        raise XMLValidatorError(f'XSD não encontrado em {xsd_path}')

    try:
        doc = etree.parse(str(xsd_path))
        _schema_cache = etree.XMLSchema(doc)
    except (etree.XMLSyntaxError, etree.XMLSchemaParseError) as exc:
        raise XMLValidatorError(f'falha ao carregar XSD oficial: {exc}') from exc
    return _schema_cache


def reset_schema_cache():
    """Usado nos testes para trocar TISS_XSD_DIR entre casos."""
    global _schema_cache
    _schema_cache = None


def validate_xml(xml_string: str) -> list[ValidationIssue]:
    """
    Retorna lista vazia se válido. Caso contrário, lista de ValidationIssue
    com linha/coluna/mensagem (via error_log do lxml) — nunca inclui o
    conteúdo do XML na mensagem de erro (pode conter dados de beneficiário).
    """
    schema = _load_schema()

    try:
        doc = etree.fromstring(xml_string.encode('utf-8'))
    except etree.XMLSyntaxError as exc:
        return [ValidationIssue(line=exc.lineno or 0, column=exc.offset or 0, message=str(exc.msg))]

    if schema.validate(doc):
        return []

    issues = []
    for error in schema.error_log:
        issues.append(ValidationIssue(line=error.line, column=error.column, message=error.message))
    return issues
