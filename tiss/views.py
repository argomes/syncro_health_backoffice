from django.db.models import Sum, Count, Q
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response

from accounts.models import ClinicAccess, SupportUser
from clinics.models import Clinic
from clinics.permissions import IsAuthenticatedByLicenseKey

from .models import TISSOperatorConfig, TISSLote, TISSGuia, TISSGlosa, TISSLoteStatus, TISSElegibilidadeConsulta
from .serializers import (
    TISSOperatorConfigSerializer, TISSLoteSerializer, TISSGuiaSerializer, TISSGlosaSerializer,
    TISSElegibilidadeConsultaSerializer,
)
from .permissions import IsTISSAuthorized
from .services import (
    enviar_lote, TISSServiceError,
    consultar_elegibilidade_automatica, registrar_elegibilidade_manual,
)


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
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
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

    Body: {operator_config_id, numero_carteira, beneficiario_nome?,
           appointment_id?, mock_scenario?}
    """
    clinic = request.clinic
    data = request.data

    operator_config_id = data.get('operator_config_id')
    numero_carteira = data.get('numero_carteira')
    if not operator_config_id or not numero_carteira:
        return Response(
            {'error': 'operator_config_id_e_numero_carteira_obrigatorios'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        # isolamento: operator_config precisa pertencer à MESMA clínica do
        # license_key autenticado — nunca consultar config de outra clínica.
        operator_config = TISSOperatorConfig.objects.get(id=operator_config_id, clinic=clinic)
    except TISSOperatorConfig.DoesNotExist:
        return Response({'error': 'operadora_nao_encontrada'}, status=status.HTTP_404_NOT_FOUND)

    consulta = consultar_elegibilidade_automatica(
        clinic=clinic,
        operator_config=operator_config,
        numero_carteira=numero_carteira,
        beneficiario_nome=data.get('beneficiario_nome', ''),
        appointment_id=data.get('appointment_id', ''),
        mock_scenario=data.get('mock_scenario', 'success'),
    )
    return Response(TISSElegibilidadeConsultaSerializer(consulta).data, status=status.HTTP_201_CREATED)


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

    operator_config_id = data.get('operator_config_id')
    numero_carteira = data.get('numero_carteira')
    numero_guia_operadora = data.get('numero_guia_operadora')
    if 'elegivel' not in data:
        return Response({'error': 'elegivel_obrigatorio'}, status=status.HTTP_400_BAD_REQUEST)
    if not operator_config_id or not numero_carteira or not numero_guia_operadora:
        return Response(
            {'error': 'operator_config_id_numero_carteira_e_numero_guia_operadora_obrigatorios'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        operator_config = TISSOperatorConfig.objects.get(id=operator_config_id, clinic=clinic)
    except TISSOperatorConfig.DoesNotExist:
        return Response({'error': 'operadora_nao_encontrada'}, status=status.HTTP_404_NOT_FOUND)

    try:
        consulta = registrar_elegibilidade_manual(
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

    return Response(TISSElegibilidadeConsultaSerializer(consulta).data, status=status.HTTP_201_CREATED)
