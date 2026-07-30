from rest_framework import serializers

from . import providers
from .models import (
    TISSOperatorConfig, TISSGatewayProvider, TISSLote, TISSGuia, TISSGlosa,
    TISSElegibilidadeConsulta, TUSSProcedureCode, ANSInsuranceOperator, mascarar_numero_carteira,
)


class TISSOperatorConfigSerializer(serializers.ModelSerializer):
    """
    Nunca expõe login_encrypted/senha_encrypted nem os valores decifrados —
    write-only via `login` / `senha` (não persistidos como estão, só cifrados).

    `endpoint_url`/`gateway_provider`/`login`/`senha` continuam expostos aqui
    pelo MESMO contrato de API de antes da separação Connection/Config
    (`.claude/tasks/TISS-MULTI-OPERATOR-STRATEGY.md` §2) — nenhum cliente da
    API (portal, admin frontend) precisa mudar. O que muda é o destino da
    escrita: `create`/`update` abaixo resolvem/reaproveitam a
    `TISSOperatorConnection` compartilhada em vez de duplicar credencial por
    config. `connection_id` fica exposto read-only para quem quiser inspecionar
    qual conexão está por trás.
    """
    login = serializers.CharField(write_only=True, required=False, allow_blank=True)
    senha = serializers.CharField(write_only=True, required=False, allow_blank=True)
    endpoint_url = serializers.URLField(required=False, allow_blank=True)
    gateway_provider = serializers.ChoiceField(
        choices=TISSGatewayProvider.choices, required=False, default=TISSGatewayProvider.DESCONHECIDO,
    )
    connection_id = serializers.UUIDField(source='connection.id', read_only=True)
    # §4.5 do documento de arquitetura: o gateway e a UI habilitam botões por
    # CAPACIDADE, nunca por nome de operadora. Sem isto, a recepção acabaria
    # com um `if operadora == 'orizon'` no frontend — o hardcode mais caro de
    # desfazer, porque vive no parque de clínicas.
    capabilities = serializers.SerializerMethodField()
    # BACFF-016: True só quando a clínica tem chamada automática de fato
    # funcional (hoje, Orizon ativo) — todas as demais configs (inclusive
    # `generico_ans`/`desconhecido`, ou Orizon com `ativo=False`) dependem de
    # confirmação manual. Ver `TISSOperatorConfig.integracao_automatica`.
    integracao_automatica = serializers.BooleanField(read_only=True)

    class Meta:
        model = TISSOperatorConfig
        fields = [
            'id', 'clinic', 'nome_operadora', 'registro_ans', 'cnpj_operadora',
            'endpoint_url', 'gateway_provider', 'connection_id', 'ativo', 'capabilities',
            'integracao_automatica', 'login', 'senha', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_capabilities(self, obj):
        # Nunca levanta: uma config apontando para provider removido do código
        # ainda precisa ser LISTÁVEL no admin/portal (é justamente a config que
        # alguém precisa consertar). O erro aparece na chamada de negócio.
        try:
            return providers.capabilities_for(obj).as_dict()
        except providers.ProviderNaoRegistrado:
            return None

    def create(self, validated_data):
        login = validated_data.pop('login', '')
        senha = validated_data.pop('senha', '')
        instance = TISSOperatorConfig(**validated_data)
        if login:
            instance.set_login(login)
        if senha:
            instance.set_senha(senha)
        instance.save()
        return instance

    def update(self, instance, validated_data):
        login = validated_data.pop('login', None)
        senha = validated_data.pop('senha', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if login:
            instance.set_login(login)
        if senha:
            instance.set_senha(senha)
        instance.save()
        return instance


class TISSGlosaSerializer(serializers.ModelSerializer):
    class Meta:
        model = TISSGlosa
        fields = ['id', 'guia', 'codigo', 'descricao', 'valor_glosado', 'recurso_enviado', 'resposta', 'created_at']
        read_only_fields = ['id', 'created_at']


class TISSGuiaSerializer(serializers.ModelSerializer):
    glosas = TISSGlosaSerializer(many=True, read_only=True)
    # BACFF-AVULSA-02: numero_carteira bruto nunca sai por essa API
    # (ReadOnlyModelViewSet — sem risco de write). O XML TISS real é
    # montado internamente a partir do model, não deste serializer.
    numero_carteira = serializers.SerializerMethodField()

    class Meta:
        model = TISSGuia
        fields = [
            'id', 'clinic', 'lote', 'appointment_id', 'tipo', 'numero', 'competencia',
            'status', 'numero_carteira', 'procedimentos', 'valor', 'glosas',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_numero_carteira(self, obj):
        return mascarar_numero_carteira(obj.numero_carteira)


class TISSLoteSerializer(serializers.ModelSerializer):
    guias = TISSGuiaSerializer(many=True, read_only=True)

    class Meta:
        model = TISSLote
        fields = [
            'id', 'clinic', 'operator_config', 'numero_lote', 'competencia', 'status',
            'protocolo', 'hash_epilogo', 'erro_mensagem', 'guias',
            'created_at', 'updated_at', 'enviado_at',
        ]
        read_only_fields = [
            'id', 'numero_lote', 'status', 'protocolo', 'hash_epilogo', 'erro_mensagem',
            'created_at', 'updated_at', 'enviado_at',
        ]


class TISSElegibilidadeConsultaSerializer(serializers.ModelSerializer):
    """Serializa o LOG operacional (BACFF-AVULSA-01) — sem conteúdo clínico."""
    class Meta:
        model = TISSElegibilidadeConsulta
        fields = ['id', 'clinic', 'operator_config', 'appointment_id', 'origem', 'status', 'erro_mensagem', 'created_at']
        read_only_fields = fields


class ElegibilidadeRespostaCompletaSerializer(serializers.Serializer):
    """
    Serializa services.ElegibilidadeRespostaCompleta — o conteúdo clínico
    completo devolvido na resposta HTTP síncrona, que NUNCA é persistido no
    banco central (BACFF-AVULSA-01). Plain Serializer (não ModelSerializer):
    não há model por trás, de propósito.
    """
    elegivel = serializers.BooleanField()
    numero_carteira = serializers.CharField()
    beneficiario_nome = serializers.CharField(allow_blank=True)
    origem = serializers.CharField()
    motivos_negativa = serializers.ListField(default=list)
    numero_guia_operadora = serializers.CharField(allow_blank=True, required=False)
    erro_mensagem = serializers.CharField(allow_blank=True, required=False)


class TUSSProcedureCodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = TUSSProcedureCode
        fields = ['tuss_code', 'description', 'table_code', 'updated_at']


class ANSInsuranceOperatorSerializer(serializers.ModelSerializer):
    class Meta:
        model = ANSInsuranceOperator
        fields = ['ans_code', 'name', 'cnpj', 'active', 'updated_at']
