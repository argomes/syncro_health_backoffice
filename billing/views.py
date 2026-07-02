from datetime import date, timedelta

from rest_framework import viewsets, filters, status
from rest_framework.decorators import action, api_view
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import ClinicAccess, SupportUser
from clinics.models import Clinic, ClinicStatus
from .models import Plan, Invoice, InvoiceStatus
from .serializers import PlanSerializer, InvoiceSerializer, InvoiceUpdateSerializer


class PlanViewSet(viewsets.ModelViewSet):
    queryset = Plan.objects.all()
    serializer_class = PlanSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']


class InvoiceViewSet(viewsets.ModelViewSet):
    queryset = Invoice.objects.all()
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ['competencia', 'status', 'due_date', 'created_at']
    ordering = ['-competencia']
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    
    def get_serializer_class(self):
        if self.action in ['partial_update']:
            return InvoiceUpdateSerializer
        return InvoiceSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Invoice.objects.select_related('clinic')

        # Isolamento de tenant: não-admins só veem faturas das suas clínicas
        if user.role != SupportUser.Role.ADMIN:
            allowed_clinic_ids = ClinicAccess.objects.filter(
                support_user=user,
                revoked_at__isnull=True,
            ).values_list('clinic_id', flat=True)
            qs = qs.filter(clinic_id__in=allowed_clinic_ids)

        clinic_id = self.request.query_params.get('clinic')
        status_filter = self.request.query_params.get('status')
        competencia = self.request.query_params.get('competencia')
        if clinic_id:
            qs = qs.filter(clinic_id=clinic_id)
        if status_filter:
            qs = qs.filter(status=status_filter)
        if competencia:
            qs = qs.filter(competencia=competencia)
        return qs

    def get_serializer_class(self):
        if self.action == 'partial_update':
            return InvoiceUpdateSerializer
        return InvoiceSerializer

    @action(detail=False, methods=['post'], url_path='generate')
    def generate(self, request):
        """
        Gera faturas mensais para todas as clínicas ativas que ainda não
        têm fatura na competência informada (ou mês atual se omitida).
        """
        competencia = request.data.get('competencia')
        if not competencia:
            today = date.today()
            competencia = today.strftime('%Y-%m')

        # Valida formato YYYY-MM
        try:
            year, month = competencia.split('-')
            assert len(year) == 4 and len(month) == 2
        except (ValueError, AssertionError):
            return Response(
                {'error': 'competencia_invalid', 'detail': 'Formato esperado: YYYY-MM'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        due_date = date(int(year), int(month), 1) + timedelta(days=30)
        active_clinics = Clinic.objects.filter(status=ClinicStatus.ACTIVE)

        created, skipped = 0, 0
        for clinic in active_clinics:
            # Busca plano pelo nome da clínica (slug simples para MVP)
            plan = Plan.objects.filter(name__iexact=clinic.plan, active=True).first()
            amount = plan.price if plan else 0

            _, was_created = Invoice.objects.get_or_create(
                clinic=clinic,
                competencia=competencia,
                defaults={'amount': amount, 'due_date': due_date},
            )
            if was_created:
                created += 1
            else:
                skipped += 1

        return Response({'created': created, 'skipped': skipped, 'competencia': competencia})

    @action(detail=True, methods=['patch'], url_path='mark-paid')
    def mark_paid(self, request, pk=None):
        """Marca fatura como paga e registra paid_at."""
        from django.utils import timezone
        invoice = self.get_object()
        invoice.status = InvoiceStatus.PAID
        invoice.paid_at = timezone.now()
        invoice.save(update_fields=['status', 'paid_at', 'updated_at'])
        return Response(InvoiceSerializer(invoice).data)

    @action(detail=False, methods=['post'], url_path='mark-overdue')
    def mark_overdue(self, request):
        """Marca como vencidas todas as faturas pendentes com due_date no passado."""
        today = date.today()
        updated = Invoice.objects.filter(
            status=InvoiceStatus.PENDING,
            due_date__lt=today,
        ).update(status=InvoiceStatus.OVERDUE)
        return Response({'marked_overdue': updated})
    

    @api_view(['POST'])
    def generate_invoices(request):
        """
        POST /api/billing/invoices/generate
        Gera faturas para todas clínicas ativas do mês
        """
        competencia = request.data.get('competencia', date.today().strftime('%Y-%m'))

        try:
            year, month = competencia.split('-')
            date(int(year), int(month), 1)
        except:
            return Response({'error': 'invalid_competencia'}, status=400)

        clinics = Clinic.objects.filter(status='active')
        created = 0

        for clinic in clinics:
            plan = Plan.objects.get(name__iexact=clinic.plan)
            invoice, was_created = Invoice.objects.get_or_create(
                clinic=clinic,
                competencia=competencia,
                defaults={
                    'amount': plan.price,
                    'due_date': date(int(year), int(month), 1) + timedelta(days=30),
                }
            )
            if was_created:
                created += 1

        return Response({'created': created, 'competencia': competencia})
