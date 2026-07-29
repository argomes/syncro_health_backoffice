# Fecha a separação Connection/Config (§2 do documento de estratégia
# multi-operadora): remove de `TISSOperatorConfig` os campos que migraram
# para `TISSOperatorConnection` (já preenchida e referenciada por 0011) e
# torna `connection` obrigatório.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tiss', '0011_backfill_tissoperatorconnection'),
    ]

    operations = [
        migrations.RemoveField(model_name='tissoperatorconfig', name='endpoint_url'),
        migrations.RemoveField(model_name='tissoperatorconfig', name='gateway_provider'),
        migrations.RemoveField(model_name='tissoperatorconfig', name='login_encrypted'),
        migrations.RemoveField(model_name='tissoperatorconfig', name='senha_encrypted'),
        migrations.AlterField(
            model_name='tissoperatorconfig',
            name='connection',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='operator_configs',
                to='tiss.tissoperatorconnection',
                help_text='Transporte/endpoint/credencial compartilhados (ex.: a Orizon desta clínica).',
            ),
        ),
    ]
