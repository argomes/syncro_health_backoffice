from rest_framework import serializers
from rest_framework.exceptions import NotFound
from .models import Ticket, TicketMessage


def _ticket_accessible(request, ticket_id):
    """
    Mesma regra de isolamento usada em TicketMessageViewSet.get_queryset
    (BO-SEC-002): retorna o Ticket se o autor da requisição (Edge via
    license_key ou support user via JWT) tem acesso a ele, senão None.
    Reaproveitada aqui para blindar o create, que antes confiava cegamente
    no ticket_id da URL.
    """
    if hasattr(request, 'clinic') and request.clinic:
        return Ticket.objects.filter(id=ticket_id, clinic=request.clinic).first()

    user = request.user
    if user and hasattr(user, 'role') and user.role == 'admin':
        return Ticket.objects.filter(id=ticket_id).first()

    if user and user.is_authenticated:
        return Ticket.objects.filter(
            id=ticket_id,
            clinic__admin_accesses__support_user=user,
        ).distinct().first()

    return None


class TicketMessageSerializer(serializers.ModelSerializer):
    author_username = serializers.CharField(source='author.username', read_only=True)

    class Meta:
        model = TicketMessage
        fields = ['id', 'message', 'author', 'author_username', 'created_at']
        read_only_fields = ['id', 'author', 'created_at']


class TicketSerializer(serializers.ModelSerializer):
    messages = TicketMessageSerializer(many=True, read_only=True)
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    assigned_to_username = serializers.CharField(source='assigned_to.username', read_only=True, allow_null=True)
    clinic_name = serializers.CharField(source='clinic.name', read_only=True)

    class Meta:
        model = Ticket
        fields = [
            'id',
            'clinic',
            'clinic_name',
            'created_by',
            'created_by_username',
            'assigned_to',
            'assigned_to_username',
            'title',
            'description',
            'status',
            'priority',
            'notion_page_id',
            'zoho_ticket_id',
            'created_at',
            'updated_at',
            'resolved_at',
            'messages',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'resolved_at', 'messages']


class TicketCreateSerializer(serializers.ModelSerializer):
    """Serializer simplificado para criar tickets"""

    class Meta:
        model = Ticket
        fields = ['clinic', 'title', 'description', 'priority']

    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


class ErrorReportCreateSerializer(serializers.Serializer):
    """
    Payload do app desktop -> Gateway -> Backoffice (EDGW-052), repassado via
    POST /error-reports do gateway e autenticado aqui por X-License-Key
    (mesmo canal de clinics/permissions.py::IsAuthenticatedByLicenseKey).

    Vira um `Ticket` local; o signal `sync_ticket_to_zoho` (BACFF-AVULSA-07)
    cuida da sincronização assíncrona com o Zoho Desk — não duplicamos essa
    lógica aqui, só usamos Ticket.objects.create() normalmente.
    """

    SEVERITY_MAP = {
        'critico': Ticket.PRIORITY[3][0],   # 'critical'
        'critica': Ticket.PRIORITY[3][0],
        'crítico': Ticket.PRIORITY[3][0],
        'crítica': Ticket.PRIORITY[3][0],
        'critical': Ticket.PRIORITY[3][0],
        'alto': Ticket.PRIORITY[2][0],      # 'high'
        'alta': Ticket.PRIORITY[2][0],
        'high': Ticket.PRIORITY[2][0],
        'medio': Ticket.PRIORITY[1][0],     # 'medium'
        'média': Ticket.PRIORITY[1][0],
        'medium': Ticket.PRIORITY[1][0],
        'baixo': Ticket.PRIORITY[0][0],     # 'low'
        'baixa': Ticket.PRIORITY[0][0],
        'low': Ticket.PRIORITY[0][0],
    }

    REPORTER_ROLE_CHOICES = ['recepcao', 'profissional', 'admin']

    category = serializers.CharField(max_length=255)
    description = serializers.CharField(max_length=8000)
    severity = serializers.CharField(max_length=20)
    reporter_role = serializers.ChoiceField(
        choices=REPORTER_ROLE_CHOICES, required=False, allow_null=True, allow_blank=True
    )
    contact_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    contact_email = serializers.EmailField(required=False, allow_blank=True)

    def validate_severity(self, value):
        key = value.strip().lower()
        if key not in self.SEVERITY_MAP:
            raise serializers.ValidationError(
                f'severity inválida: use uma das seguintes: {sorted(set(self.SEVERITY_MAP))}'
            )
        return self.SEVERITY_MAP[key]

    def validate_category(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('category não pode ser vazio.')
        return value

    def validate_description(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('description não pode ser vazio.')
        return value

    def create(self, validated_data):
        clinic = self.context['clinic']

        contact_name = validated_data.get('contact_name', '').strip()
        contact_email = validated_data.get('contact_email', '').strip()
        reporter_role = validated_data.get('reporter_role') or ''

        # Metadados de contexto (role/contato) não têm campo dedicado no
        # model Ticket hoje — anexados como cabeçalho estruturado no início
        # da descrição em vez de criar migração nova só para isto. A
        # descrição em si pode conter PHI citada pelo usuário (regra 4.1 do
        # CLAUDE.md) — nunca é logada, só persistida no Ticket.
        meta_lines = [f'[Origem: App Desktop] [Categoria: {validated_data["category"]}]']
        if reporter_role:
            meta_lines.append(f'[Role: {reporter_role}]')
        if contact_name:
            meta_lines.append(f'[Contato: {contact_name}]')
        if contact_email:
            meta_lines.append(f'[Email: {contact_email}]')

        full_description = '\n'.join(meta_lines) + '\n\n' + validated_data['description']

        title = f'[Erro Desktop] {validated_data["category"]}'[:255]

        return Ticket.objects.create(
            clinic=clinic,
            created_by=None,
            title=title,
            description=full_description,
            priority=validated_data['severity'],
        )


class TicketMessageCreateSerializer(serializers.ModelSerializer):
    """Serializer para adicionar mensagens"""

    class Meta:
        model = TicketMessage
        fields = ['message']

    def validate(self, attrs):
        request = self.context['request']
        ticket_id = self.context['ticket_id']
        # BO-SEC-008: bloqueia POST de mensagem em ticket de outra clínica —
        # antes o create confiava cegamente no ticket_id da URL.
        if _ticket_accessible(request, ticket_id) is None:
            # 404 em vez de 403 — não confirma nem nega a existência do
            # ticket alheio, mesmo espírito do isolamento em get_queryset.
            raise NotFound('Ticket não encontrado.')
        return attrs

    def create(self, validated_data):
        ticket_id = self.context['ticket_id']
        validated_data['ticket_id'] = ticket_id
        validated_data['author'] = self.context['request'].user
        return super().create(validated_data)
