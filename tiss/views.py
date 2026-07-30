from django.db.models import Sum, Count, Q
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.response import Response

from accounts.models import ClinicAccess, SupportUser
from clinics.models import Clinic
from clinics.permissions import IsAuthenticatedByLicenseKey
from syncro_backoffice.throttling import ReferenceDataRateThrottle

from .models import (
    TISSOperatorConfig, TISSLote, TISSGuia, TISSGlosa, TISSLoteStatus, TISSElegibilidadeConsulta,
    TUSSProcedureCode, ANSInsuranceOperator,
)
from .serializers import (
    TISSOperatorConfigSerializer, TISSLoteSerializer, TISSGuiaSerializer, TISSGlosaSerializer,
    TISSElegibilidadeConsultaSerializer, TUSSProcedureCodeSerializer, ANSInsuranceOperatorSerializer,
    ElegibilidadeRespostaCompletaSerializer,
    TISSDocumentoAssinaturaPendenteSerializer, TISSDocumentoAssinaturaBlocoSerializer,
)
from .permissions import IsTISSAuthorized
from .services import (
    enviar_lote, TISSServiceError,
    consultar_elegibilidade_automatica, registrar_elegibilidade_manual,
    disparar_cancelamento_guia,
)
from . import xmldsig_service
from .xmldsig_service import XMLDSigServiceError


# Códigos de erro que significam "a config desta operadora bloqueia a chamada
# automática" — estado de configuração, não erro de validação do pedido. Vão
# como **409 Conflict** (deliberadamente NÃO 404, NÃO 422) para que o gateway
# Go possa distinguir "operadora não cadastrada" (404) de "cadastrada mas
# indisponível por configuração" (409) e mostrar a mensagem certa na
# recepção — inclusive sugerir o registro manual, que continua permitido (D2).
_ERROS_CONFIG_OPERADORA_409 = frozenset({
    'operadora_desativada',    # ativo=False (§4.2 nível 1)
    'provider_nao_confirmado',  # gateway_provider='desconhecido' (D3)
    'provider_nao_registrado',  # provider removido do código (§4.2 nível 2)
})


def _http_status_para_erro_tiss(code: str) -> int:
    return status.HTTP_409_CONFLICT if code in _ERROS_CONFIG_OPERADORA_409 else status.HTTP_422_UNPROCESSABLE_ENTITY


def _allowed_clinic_ids(user):
    """Mesma regra de isolamento usada em billing/views.py::InvoiceViewSet — admin vê tudo, os demais só suas clínicas."""
    if user.role == SupportUser.Role.ADMIN:
        return None  # sinaliza "sem filtro"
    return ClinicAccess.objects.filter(support_user=user, revoked_at__isnull=True).values_list('clinic_id', flat=True)


class TISSOperatorConfigViewSet(viewsets.ModelViewSet):
    serializer_class = TISSOperatorConfigSerializer
    permission_classes = [IsTISSAuthorized]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        qs = TISSOperatorConfig.objects.select_related('clinic')
        allowed = _allowed_clinic_ids(self.request.user)
        if allowed is not None:
            qs = qs.filter(clinic_id__in=allowed)
        clinic_id = self.request.query_params.get('clinic')
        if clinic_id:
            qs = qs.filter(clinic_id=clinic_id)
        return qs


class TISSLoteViewSet(viewsets.ModelViewSet):
    serializer_class = TISSLoteSerializer
    permission_classes = [IsTISSAuthorized]
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        qs = TISSLote.objects.select_related('clinic', 'operator_config').prefetch_related('guias', 'guias__glosas')
        allowed = _allowed_clinic_ids(self.request.user)
        if allowed is not None:
            qs = qs.filter(clinic_id__in=allowed)
        clinic_id = self.request.query_params.get('clinic')
        competencia = self.request.query_params.get('competencia')
        status_filter = self.request.query_params.get('status')
        if clinic_id:
            qs = qs.filter(clinic_id=clinic_id)
        if competencia:
            qs = qs.filter(competencia=competencia)
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs

    @action(detail=True, methods=['post'])
    def enviar(self, request, pk=None):
        """
        POST /api/tiss/lotes/{id}/enviar/ — busca guias do lote (já isoladas
        por clínica via get_queryset/get_object), monta XML, valida XSD,
        calcula MD5, envia SOAP (real ou mock via TISS_SOAP_MOCK) e persiste.
        """
        lote = self.get_object()
        mock_scenario = request.data.get('mock_scenario', 'success')
        try:
            lote = enviar_lote(lote, mock_scenario=mock_scenario)
        except TISSServiceError as exc:
            return Response(
                {'error': exc.code, 'detail': str(exc)},
                status=_http_status_para_erro_tiss(exc.code),
            )
        return Response(TISSLoteSerializer(lote).data)


class TISSGuiaViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TISSGuiaSerializer
    permission_classes = [IsTISSAuthorized]

    def get_queryset(self):
        qs = TISSGuia.objects.select_related('clinic', 'lote').prefetch_related('glosas')
        allowed = _allowed_clinic_ids(self.request.user)
        if allowed is not None:
            qs = qs.filter(clinic_id__in=allowed)
        clinic_id = self.request.query_params.get('clinic')
        competencia = self.request.query_params.get('competencia')
        status_filter = self.request.query_params.get('status')
        if clinic_id:
            qs = qs.filter(clinic_id=clinic_id)
        if competencia:
            qs = qs.filter(competencia=competencia)
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs


@api_view(['POST'])
@permission_classes([IsAuthenticatedByLicenseKey])
def cancelar_guia_view(request):
    """
    POST /api/tiss/guias/cancelar/ — BACFF-014 (2026-07-30). Consumido pelo
    Edge Gateway (autenticado por license_key, mesmo padrão de
    `verificar_elegibilidade`) quando a recepção cancela um
    atendimento/agendamento que já tinha guia/autorização Orizon emitida.
    Dispara o cancelamento automático junto à operadora (assíncrono, via
    Celery — não bloqueia a resposta ao gateway).

    Body: {appointment_id}. Identifica a guia por (clinic, appointment_id) —
    isolamento multi-tenant: nunca cancela guia de outra clínica.
    """
    clinic = request.clinic
    appointment_id = request.data.get('appointment_id')
    if not appointment_id:
        return Response({'error': 'appointment_id_obrigatorio'}, status=status.HTTP_400_BAD_REQUEST)

    guia = (
        TISSGuia.objects.filter(clinic=clinic, appointment_id=appointment_id)
        .order_by('-created_at')
        .first()
    )
    if guia is None:
        return Response({'error': 'guia_nao_encontrada'}, status=status.HTTP_404_NOT_FOUND)

    cancelamento_enfileirado = disparar_cancelamento_guia(guia)
    return Response(
        {'guia_id': str(guia.id), 'cancelamento_enfileirado': cancelamento_enfileirado},
        status=status.HTTP_202_ACCEPTED,
    )


@api_view(['GET'])
@permission_classes([IsTISSAuthorized])
def estatisticas(request):
    """
    GET /api/tiss/estatisticas/?clinic=&competencia=
    Isolado por clínica: um SupportUser não-admin só vê estatísticas das
    clínicas às quais tem ClinicAccess ativo — mesmo filtro de get_queryset
    acima, aplicado diretamente na query de agregação.
    """
    user = request.user
    allowed = _allowed_clinic_ids(user)

    lotes_qs = TISSLote.objects.all()
    guias_qs = TISSGuia.objects.all()
    glosas_qs = TISSGlosa.objects.all()

    if allowed is not None:
        lotes_qs = lotes_qs.filter(clinic_id__in=allowed)
        guias_qs = guias_qs.filter(clinic_id__in=allowed)
        glosas_qs = glosas_qs.filter(guia__clinic_id__in=allowed)

    clinic_id = request.query_params.get('clinic')
    competencia = request.query_params.get('competencia')
    if clinic_id:
        lotes_qs = lotes_qs.filter(clinic_id=clinic_id)
        guias_qs = guias_qs.filter(clinic_id=clinic_id)
        glosas_qs = glosas_qs.filter(guia__clinic_id=clinic_id)
    if competencia:
        lotes_qs = lotes_qs.filter(competencia=competencia)
        guias_qs = guias_qs.filter(competencia=competencia)
        glosas_qs = glosas_qs.filter(guia__competencia=competencia)

    total_lotes = lotes_qs.count()
    valor_apresentado = guias_qs.aggregate(total=Sum('valor'))['total'] or 0
    valor_glosado = glosas_qs.aggregate(total=Sum('valor_glosado'))['total'] or 0
    valor_aceito = float(valor_apresentado) - float(valor_glosado)
    taxa_glosa = round((float(valor_glosado) / float(valor_apresentado) * 100), 2) if valor_apresentado else 0.0

    top_glosas = list(
        glosas_qs.values('codigo', 'descricao')
        .annotate(total=Count('id'), valor=Sum('valor_glosado'))
        .order_by('-valor')[:10]
    )

    por_operadora = []
    operadoras = lotes_qs.values('operator_config__nome_operadora').distinct()
    for op in operadoras:
        nome = op['operator_config__nome_operadora']
        lotes_op = lotes_qs.filter(operator_config__nome_operadora=nome)
        guias_op = guias_qs.filter(lote__in=lotes_op)
        por_operadora.append({
            'nome': nome,
            'enviados': guias_op.filter(status='enviada').count() + guias_op.filter(status='aceita').count() + guias_op.filter(status='glosada').count() + guias_op.filter(status='parcial').count(),
            'aceitos': guias_op.filter(status='aceita').count(),
            'glosados': guias_op.filter(status='glosada').count(),
        })

    return Response({
        'total_lotes': total_lotes,
        'valor_apresentado': float(valor_apresentado),
        'valor_aceito': valor_aceito,
        'valor_glosado': float(valor_glosado),
        'taxa_glosa': taxa_glosa,
        'top_glosas': [
            {'codigo': g['codigo'], 'descricao': g['descricao'], 'total': g['total'], 'valor': float(g['valor'] or 0)}
            for g in top_glosas
        ],
        'por_operadora': por_operadora,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedByLicenseKey])
def verificar_elegibilidade(request):
    """
    POST /api/tiss/elegibilidade/verificar/ — consumido pelo Edge Gateway
    (autenticado por license_key, mesmo padrão de metrics.heartbeat), nunca
    diretamente por um usuário do backoffice. Consulta síncrona: a
    recepcionista está esperando a resposta na hora (BACFF-013 — diferente
    do fluxo de lote, que é assíncrono).

    Body: {registro_ans, numero_carteira, beneficiario_nome?,
           appointment_id?, mock_scenario?}

    Identifica a operadora por `registro_ans` (código ANS de 6 dígitos), não
    pelo UUID interno do TISSOperatorConfig — é a chave de negócio que o
    Gateway já tem localmente (domain.InsuranceOperator.ANSCode), evitando
    acoplar os dois sistemas pelos IDs internos de cada um.
    """
    clinic = request.clinic
    data = request.data

    registro_ans = data.get('registro_ans')
    numero_carteira = data.get('numero_carteira')
    if not registro_ans or not numero_carteira:
        return Response(
            {'error': 'registro_ans_e_numero_carteira_obrigatorios'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        # isolamento: operator_config precisa pertencer à MESMA clínica do
        # license_key autenticado — nunca consultar config de outra clínica.
        operator_config = TISSOperatorConfig.objects.get(registro_ans=registro_ans, clinic=clinic)
    except TISSOperatorConfig.DoesNotExist:
        return Response({'error': 'operadora_nao_encontrada'}, status=status.HTTP_404_NOT_FOUND)

    try:
        resultado = consultar_elegibilidade_automatica(
            clinic=clinic,
            operator_config=operator_config,
            numero_carteira=numero_carteira,
            beneficiario_nome=data.get('beneficiario_nome', ''),
            appointment_id=data.get('appointment_id', ''),
            mock_scenario=data.get('mock_scenario', 'success'),
        )
    except TISSServiceError as exc:
        return Response({'error': exc.code, 'detail': str(exc)}, status=_http_status_para_erro_tiss(exc.code))
    return Response(ElegibilidadeRespostaCompletaSerializer(resultado).data, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticatedByLicenseKey])
@throttle_classes([ReferenceDataRateThrottle])
def procedure_code_lookup(request, tuss_code):
    """
    GET /api/tiss/reference/procedure-codes/{tuss_code}/ — consumido pelo
    Edge Gateway em cache-aside: o gateway busca aqui só quando não encontra
    localmente, e persiste a resposta no SQLite da clínica para não bater
    aqui de novo no mesmo código. Sem escopo de clínica — dado regulatório
    público (TUSS), igual pra todos.
    """
    try:
        code = TUSSProcedureCode.objects.get(tuss_code=tuss_code)
    except TUSSProcedureCode.DoesNotExist:
        return Response({'error': 'codigo_tuss_nao_encontrado'}, status=status.HTTP_404_NOT_FOUND)
    return Response(TUSSProcedureCodeSerializer(code).data)


@api_view(['GET'])
@permission_classes([IsAuthenticatedByLicenseKey])
@throttle_classes([ReferenceDataRateThrottle])
def insurance_operator_lookup(request, ans_code):
    """
    GET /api/tiss/reference/operators/{ans_code}/ — mesmo padrão cache-aside
    de procedure_code_lookup, para operadoras (registro ANS).
    """
    try:
        operator = ANSInsuranceOperator.objects.get(ans_code=ans_code)
    except ANSInsuranceOperator.DoesNotExist:
        return Response({'error': 'operadora_ans_nao_encontrada'}, status=status.HTTP_404_NOT_FOUND)
    return Response(ANSInsuranceOperatorSerializer(operator).data)


@api_view(['GET'])
@permission_classes([IsAuthenticatedByLicenseKey])
@throttle_classes([ReferenceDataRateThrottle])
def procedure_code_search(request):
    """
    GET /api/tiss/reference/procedure-codes/search/?q=... — usado pelo
    gateway quando a busca LOCAL (SQLite da clínica) não encontra nada:
    a tabela completa (~6 mil códigos TUSS 22) só existe aqui, o gateway
    nunca replica ela inteira, só cacheia sob demanda o que a clínica usou.
    """
    q = request.query_params.get('q', '').strip()
    if not q:
        return Response({'error': 'parametro_q_obrigatorio'}, status=status.HTTP_400_BAD_REQUEST)
    codes = TUSSProcedureCode.objects.filter(
        Q(tuss_code__icontains=q) | Q(description__icontains=q)
    ).order_by('description')[:50]
    return Response(TUSSProcedureCodeSerializer(codes, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticatedByLicenseKey])
@throttle_classes([ReferenceDataRateThrottle])
def insurance_operator_search(request):
    """
    GET /api/tiss/reference/operators/search/?q=... — mesmo padrão de
    procedure_code_search, para operadoras por nome/registro ANS.
    """
    q = request.query_params.get('q', '').strip()
    if not q:
        return Response({'error': 'parametro_q_obrigatorio'}, status=status.HTTP_400_BAD_REQUEST)
    operators = ANSInsuranceOperator.objects.filter(
        Q(name__icontains=q) | Q(ans_code__icontains=q)
    ).order_by('name')[:50]
    return Response(ANSInsuranceOperatorSerializer(operators, many=True).data)


@api_view(['GET'])
@permission_classes([IsAuthenticatedByLicenseKey])
def sync_documentos_pendentes(request):
    """
    TASK-BO-10 — GET /api/tiss/xmldsig/sync/pendentes/ — pull do SyncWorker
    do gateway (Go, EDGW-073, fora desta task). Mesmo padrão de autenticação
    dos demais endpoints consumidos pelo gateway (X-License-Key, ver
    `verificar_elegibilidade` acima) — não inventa esquema de auth novo.
    Devolve os fragmentos C14N pendentes de assinatura desta clínica; o
    gateway assina localmente (o backoffice nunca vê o .p12) e devolve só o
    bloco de assinatura no push seguinte (`sync_documentos_assinatura`).
    """
    clinic = request.clinic
    pendentes = xmldsig_service.listar_pendentes_para_sync(clinic)
    return Response({
        'documentos': TISSDocumentoAssinaturaPendenteSerializer(pendentes, many=True).data,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticatedByLicenseKey])
def sync_documentos_assinatura(request):
    """
    TASK-BO-10 — POST /api/tiss/xmldsig/sync/assinatura/ — push do
    SyncWorker do gateway devolvendo SÓ o bloco de assinatura
    (SignedInfo/SignatureValue/KeyInfo) — nunca o XML completo nem o
    certificado. Reinserido por texto em `fragmento_canonico`
    (`TISSDocumentoAssinatura.aplicar_bloco_assinatura`), nunca reparseado.
    Isolado por clínica: `documento_id` de outra clínica é tratado como
    "não encontrado" (404), nunca vaza existência entre clínicas.
    """
    serializer = TISSDocumentoAssinaturaBlocoSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    clinic = request.clinic

    try:
        documento = xmldsig_service.aplicar_assinatura(
            clinic=clinic,
            documento_id=serializer.validated_data['documento_id'],
            signature_block=serializer.validated_data['signature_block'],
        )
    except XMLDSigServiceError as exc:
        http_status = status.HTTP_404_NOT_FOUND if exc.code == 'documento_nao_encontrado' else status.HTTP_422_UNPROCESSABLE_ENTITY
        return Response({'error': exc.code, 'detail': str(exc)}, status=http_status)

    return Response({'id': str(documento.id), 'status': documento.status})


@api_view(['POST'])
@permission_classes([IsAuthenticatedByLicenseKey])
def registrar_elegibilidade_manual_view(request):
    """
    POST /api/tiss/elegibilidade/manual/ — fallback de primeira classe
    (BACFF-013): recepcionista confirmou por telefone/portal da operadora e
    digitou o número da guia gerado manualmente. numero_guia_operadora é
    obrigatório (validado em services.registrar_elegibilidade_manual e no
    model TISSElegibilidadeConsulta.clean()).
    """
    clinic = request.clinic
    data = request.data

    registro_ans = data.get('registro_ans')
    numero_carteira = data.get('numero_carteira')
    numero_guia_operadora = data.get('numero_guia_operadora')
    if 'elegivel' not in data:
        return Response({'error': 'elegivel_obrigatorio'}, status=status.HTTP_400_BAD_REQUEST)
    if not registro_ans or not numero_carteira or not numero_guia_operadora:
        return Response(
            {'error': 'registro_ans_numero_carteira_e_numero_guia_operadora_obrigatorios'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        operator_config = TISSOperatorConfig.objects.get(registro_ans=registro_ans, clinic=clinic)
    except TISSOperatorConfig.DoesNotExist:
        return Response({'error': 'operadora_nao_encontrada'}, status=status.HTTP_404_NOT_FOUND)

    try:
        resultado = registrar_elegibilidade_manual(
            clinic=clinic,
            operator_config=operator_config,
            numero_carteira=numero_carteira,
            numero_guia_operadora=numero_guia_operadora,
            elegivel=bool(data.get('elegivel')),
            beneficiario_nome=data.get('beneficiario_nome', ''),
            appointment_id=data.get('appointment_id', ''),
        )
    except TISSServiceError as exc:
        return Response({'error': exc.code, 'detail': str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    return Response(ElegibilidadeRespostaCompletaSerializer(resultado).data, status=status.HTTP_201_CREATED)
