"""
D3 (decisão do Tech Lead, 2026-07-28) — `generico_ans` deixa de ser o
default silencioso de `TISSOperatorConfig.gateway_provider`.

Duas partes:
1. Schema: adiciona a escolha `desconhecido` e a torna o novo default.
2. Dados: migra as configs existentes que estão em `generico_ans` para
   `desconhecido`.

Por que a migração de dados é segura (e por que ela é o ponto todo). Até
aqui, `generico_ans` era o DEFAULT do model — ou seja, é impossível
distinguir "alguém escolheu o dialeto genérico depois de confirmar o manual
da operadora" de "ninguém escolheu nada e o Django preencheu". Como
nenhuma operadora real jamais confirmou compatibilidade com o dialeto
genérico (e o client genérico sequer envia credencial — buraco B4 do
documento de arquitetura), tratamos TODAS essas configs como não
confirmadas. O efeito para elas é: chamada automática passa a falhar com
`provider_nao_confirmado` (409) em vez de mandar payload num dialeto que a
operadora provavelmente rejeita — e o registro manual continua funcionando
(D2), então a recepção não para.

Configs `orizon` NÃO são tocadas: a Orizon tem provider próprio, dialeto
confirmado contra manual técnico oficial, e continua funcionando
exatamente como antes.

A reversão restaura `generico_ans`, mas só para as configs que esta
migration mexeu não é rastreável — reverter devolve TODOS os
`desconhecido` para `generico_ans`, restaurando o comportamento anterior
(inclusive o bug). Aceitável porque a reversão é um rollback de deploy,
não uma operação de rotina.
"""
from django.db import migrations, models


def generico_ans_para_desconhecido(apps, schema_editor):
    TISSOperatorConfig = apps.get_model('tiss', 'TISSOperatorConfig')
    TISSOperatorConfig.objects.filter(gateway_provider='generico_ans').update(
        gateway_provider='desconhecido',
    )


def desconhecido_para_generico_ans(apps, schema_editor):
    TISSOperatorConfig = apps.get_model('tiss', 'TISSOperatorConfig')
    TISSOperatorConfig.objects.filter(gateway_provider='desconhecido').update(
        gateway_provider='generico_ans',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('tiss', '0007_tissoperatorconfig_gateway_provider'),
    ]

    operations = [
        migrations.AlterField(
            model_name='tissoperatorconfig',
            name='gateway_provider',
            field=models.CharField(
                choices=[
                    ('desconhecido', 'Desconhecido (não confirmado — bloqueia chamada automática)'),
                    ('generico_ans', 'Genérico (padrão ANS) — só com compatibilidade confirmada'),
                    ('orizon', 'Orizon (Autorize)'),
                ],
                default='desconhecido',
                help_text=(
                    'Qual client SOAP usar para esta operadora. Deixe em "Desconhecido" '
                    'até confirmar o dialeto contra o manual técnico oficial da operadora: '
                    'nesse estado a chamada automática é bloqueada com erro explícito e a '
                    'recepção usa o registro manual, em vez de mandar payload no dialeto errado.'
                ),
                max_length=20,
            ),
        ),
        migrations.RunPython(generico_ans_para_desconhecido, desconhecido_para_generico_ans),
    ]
