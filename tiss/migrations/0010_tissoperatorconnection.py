# BACFF — correção do defeito de credencial duplicada
# (`.claude/tasks/TISS-MULTI-OPERATOR-STRATEGY.md` §2): separa transporte
# /endpoint/credencial (`TISSOperatorConnection`, novo) de particularidade da
# operadora real (`TISSOperatorConfig`, existente). Esta migration só cria o
# schema novo e o vínculo (nullable) — os dados existentes são migrados na
# migration seguinte (0011), e os campos antigos só são removidos de
# `TISSOperatorConfig` na migration depois dessa (0012). Split em três passos
# de propósito: permite backfill de dados reais sem perder nada no meio do
# caminho.
import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clinics', '0009_clinic_support_ticket_restricted_to_admin'),
        ('tiss', '0009_operator_call_log'),
    ]

    operations = [
        migrations.CreateModel(
            name='TISSOperatorConnection',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('endpoint_url', models.URLField(max_length=255)),
                (
                    'gateway_provider',
                    models.CharField(
                        choices=[
                            ('desconhecido', 'Desconhecido (não confirmado — bloqueia chamada automática)'),
                            ('generico_ans', 'Genérico (padrão ANS) — só com compatibilidade confirmada'),
                            ('orizon', 'Orizon (Autorize)'),
                        ],
                        default='desconhecido',
                        help_text=(
                            'Qual client SOAP usar para este transporte. Deixe em "Desconhecido" '
                            'até confirmar o dialeto contra o manual técnico oficial: nesse estado '
                            'a chamada automática é bloqueada com erro explícito e a recepção usa '
                            'o registro manual, em vez de mandar payload no dialeto errado.'
                        ),
                        max_length=20,
                    ),
                ),
                ('login_encrypted', models.TextField(blank=True)),
                ('senha_encrypted', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'clinic',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='tiss_operator_connections',
                        to='clinics.clinic',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Conexão de Operadora TISS',
                'verbose_name_plural': 'Conexões de Operadora TISS',
            },
        ),
        migrations.AddField(
            model_name='tissoperatorconfig',
            name='connection',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='operator_configs',
                to='tiss.tissoperatorconnection',
                help_text='Transporte/endpoint/credencial compartilhados (ex.: a Orizon desta clínica).',
            ),
        ),
        migrations.AlterUniqueTogether(
            name='tissoperatorconnection',
            unique_together={('clinic', 'endpoint_url', 'gateway_provider')},
        ),
    ]
