"""
TASK-BO-10 — Orquestra o fluxo assíncrono de assinatura XMLDSig de
`envioDocumentoWS`. Módulo dedicado (não `tiss/services.py`) porque este
fluxo é ortogonal ao de lote/elegibilidade: envolve um handoff assíncrono
com o gateway (fila de fragmentos pendentes + bloco de assinatura devolvido
depois), não uma chamada SOAP síncrona de request/response.

Este arquivo NÃO sabe qual operadora está por trás — despacha via
`providers.resolve()` (§5 do documento de arquitetura, o mesmo desenho de
`tiss/services.py::enviar_lote`), exatamente como todo o resto do módulo TISS.
Hoje só a Orizon implementa `preparar_documento_assinatura`/
`enviar_documento_assinado` de verdade (é a única operação Orizon que exige
XMLDSig); qualquer outro provider devolve `OperacaoNaoSuportada` — ver
`tiss/providers/orizon.py`, `tiss/providers/desconhecido.py`,
`tiss/providers/generico_ans.py`. Plugar uma operadora nova com esta
capacidade não deve exigir tocar neste arquivo.

Fluxo:
1. `enfileirar_documento` — resolve o provider, monta o fragmento C14N (sem
   <Signature>) e cria o registro PENDENTE_ASSINATURA.
2. `listar_pendentes_para_sync` — usado pelo endpoint de pull
   (`tiss/views.py::sync_documentos_pendentes`), consumido pelo SyncWorker do
   gateway (lado Go, EDGW-073, fora desta task).
3. `aplicar_assinatura` — usado pelo endpoint de push
   (`tiss/views.py::sync_documentos_assinatura`); delega a reinserção textual
   para `TISSDocumentoAssinatura.aplicar_bloco_assinatura` (nunca reparseia).
4. `transmitir` — resolve o provider e envia `xml_final` via
   `enviar_documento_assinado` (mesmo client SOAP de BO-08/BO-09 por baixo).
"""
import logging

from django.db import transaction
from django.utils import timezone

from .models import TISSDocumentoAssinatura, TISSDocumentoAssinaturaStatus
from . import providers
from .providers.base import ProviderError

logger = logging.getLogger(__name__)


class XMLDSigServiceError(Exception):
    def __init__(self, code: str, message: str = ''):
        self.code = code
        super().__init__(message or code)


def enfileirar_documento(
    *, clinic, guia, operator_config, sequencial_transacao: str,
    documento_base64: str, nome_arquivo: str, tipo_documento: str = 'ANEXO',
) -> TISSDocumentoAssinatura:
    """
    Resolve o provider da operadora, monta o fragmento C14N (sem
    <Signature>) e cria o registro PENDENTE_ASSINATURA associado à clínica.
    Isolamento: `guia` e `operator_config` DEVEM pertencer à mesma `clinic`
    (garantido por `TISSDocumentoAssinatura.clean`, chamado via `full_clean`).
    """
    try:
        provider = providers.resolve(operator_config)
        fragmento = provider.preparar_documento_assinatura(
            guia, clinic, sequencial_transacao,
            documento_base64, nome_arquivo, tipo_documento=tipo_documento,
        )
    except ProviderError as exc:
        raise XMLDSigServiceError(getattr(exc, 'code', 'provider_error'), str(exc)) from exc

    documento = TISSDocumentoAssinatura(
        clinic=clinic,
        guia=guia,
        operator_config=operator_config,
        status=TISSDocumentoAssinaturaStatus.PENDENTE_ASSINATURA,
        fragmento_canonico=fragmento.fragmento_canonico,
        root_tag=fragmento.root_tag,
        sequencial_transacao=sequencial_transacao,
    )
    documento.full_clean()
    documento.save()
    return documento


def listar_pendentes_para_sync(clinic, limit: int = 50):
    """
    Fragmentos PENDENTE_ASSINATURA da clínica, mais antigos primeiro — é o
    que o endpoint de pull expõe ao SyncWorker do gateway.
    """
    return list(
        TISSDocumentoAssinatura.objects
        .filter(clinic=clinic, status=TISSDocumentoAssinaturaStatus.PENDENTE_ASSINATURA)
        .order_by('created_at')[:limit]
    )


def aplicar_assinatura(clinic, documento_id: str, signature_block: str) -> TISSDocumentoAssinatura:
    """
    Reinsere o bloco de assinatura devolvido pelo gateway. Isolado por
    clínica: nunca aceita `documento_id` de outra clínica (mesmo padrão de
    isolamento multi-tenant do resto do app — `get_object_or_404` scoped).
    """
    try:
        documento = TISSDocumentoAssinatura.objects.get(id=documento_id, clinic=clinic)
    except TISSDocumentoAssinatura.DoesNotExist as exc:
        raise XMLDSigServiceError('documento_nao_encontrado') from exc

    if documento.status != TISSDocumentoAssinaturaStatus.PENDENTE_ASSINATURA:
        raise XMLDSigServiceError(
            'documento_nao_esta_pendente',
            f'status atual: {documento.status}',
        )

    with transaction.atomic():
        documento.aplicar_bloco_assinatura(signature_block)
    return documento


def transmitir(documento: TISSDocumentoAssinatura, mock_scenario: str = 'success') -> TISSDocumentoAssinatura:
    """
    Resolve o provider da operadora e envia `documento.xml_final` — por
    baixo, mesmo client SOAP de BO-08/BO-09
    (`tiss/soap_client.py::enviar_documento`, chamado via
    `providers/orizon.py::enviar_documento_assinado`). `xml_final` é a
    string produzida por `aplicar_bloco_assinatura` (fragmento_canonico +
    signature_block por concatenação textual); nunca é reparseada/
    re-serializada aqui.
    """
    if documento.status != TISSDocumentoAssinaturaStatus.ASSINADO:
        raise XMLDSigServiceError('documento_nao_assinado', f'status atual: {documento.status}')

    try:
        provider = providers.resolve(documento.operator_config)
        resultado = provider.enviar_documento_assinado(documento.xml_final, mock_scenario=mock_scenario)
    except ProviderError as exc:
        documento.status = TISSDocumentoAssinaturaStatus.ERRO_ENVIO
        documento.erro_mensagem = getattr(exc, 'code', 'provider_error')
        documento.save(update_fields=['status', 'erro_mensagem', 'updated_at'])
        raise XMLDSigServiceError(getattr(exc, 'code', 'provider_error'), str(exc)) from exc

    if resultado.sucesso:
        documento.status = TISSDocumentoAssinaturaStatus.ENVIADO
        documento.protocolo = resultado.protocolo
        documento.enviado_at = timezone.now()
        documento.save(update_fields=['status', 'protocolo', 'enviado_at', 'updated_at'])
        return documento

    documento.status = TISSDocumentoAssinaturaStatus.ERRO_ENVIO
    documento.erro_mensagem = f'{resultado.erro_code}: {resultado.erro_mensagem}'
    documento.save(update_fields=['status', 'erro_mensagem', 'updated_at'])
    return documento
