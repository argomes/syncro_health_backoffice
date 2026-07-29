# Data migration do defeito de credencial duplicada (§2 do documento de
# estratégia multi-operadora). Agrupa as `TISSOperatorConfig` existentes por
# (clinic, endpoint_url, gateway_provider) — linhas do mesmo grupo
# compartilhavam endpoint e credencial idênticos (ex.: 5 operadoras reais
# atrás da mesma Orizon) e passam a apontar para UMA `TISSOperatorConnection`
# só, criada a partir da primeira linha do grupo (login/senha já vêm cifrados
# — copiados como estão, sem decifrar/recifrar).
#
# Reversível: a reversão apenas limpa `connection_id` de volta para NULL — os
# campos antigos (`endpoint_url`, `gateway_provider`, `login_encrypted`,
# `senha_encrypted`) ainda existem em `TISSOperatorConfig` neste ponto da
# migration history (só são removidos na 0012), então nenhum dado é perdido
# ao reverter até aqui.
from django.db import migrations


def backfill(apps, schema_editor):
    TISSOperatorConfig = apps.get_model('tiss', 'TISSOperatorConfig')
    TISSOperatorConnection = apps.get_model('tiss', 'TISSOperatorConnection')

    grupos = {}
    for config in TISSOperatorConfig.objects.all().order_by('created_at'):
        chave = (config.clinic_id, config.endpoint_url, config.gateway_provider)
        connection_id = grupos.get(chave)
        if connection_id is None:
            connection = TISSOperatorConnection.objects.create(
                clinic_id=config.clinic_id,
                endpoint_url=config.endpoint_url,
                gateway_provider=config.gateway_provider,
                login_encrypted=config.login_encrypted,
                senha_encrypted=config.senha_encrypted,
            )
            connection_id = connection.pk
            grupos[chave] = connection_id
        config.connection_id = connection_id
        config.save(update_fields=['connection'])


def reverse(apps, schema_editor):
    TISSOperatorConfig = apps.get_model('tiss', 'TISSOperatorConfig')
    TISSOperatorConfig.objects.update(connection=None)


class Migration(migrations.Migration):

    dependencies = [
        ('tiss', '0010_tissoperatorconnection'),
    ]

    operations = [
        migrations.RunPython(backfill, reverse),
    ]
