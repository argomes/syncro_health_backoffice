from rest_framework import serializers

from .models import (
    TISSOperatorConfig, TISSLote, TISSGuia, TISSGlosa, TISSElegibilidadeConsulta,
    TUSSProcedureCode, ANSInsuranceOperator, mascarar_numero_carteira,
)


class TISSOperatorConfigSerializer(serializers.ModelSerializer):
    """
    Nunca expõe login_encrypted/senha_encrypted nem os valores decifrados —
    write-only via `login` / `senha` (não persistidos como estão, só cifrados).
    """
    login = serializers.CharField(write_only=True, required=False, allow_blank=True)
    senha = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = TISSOperatorConfig
        fields = [
            'id', 'clinic', 'nome_operadora', 'registro_ans', 'cnpj_operadora',
            'endpoint_url', 'ativo', 'login', 'senha', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

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
