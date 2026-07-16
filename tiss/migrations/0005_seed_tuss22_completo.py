import csv
from pathlib import Path

from django.db import migrations

# Fonte: TUSS 22 - PROCEDIMENTOS E EVENTOS EM SAÚDE - VERSÃO 202601 (ANS,
# oficial). Exportado uma única vez para CSV em tiss/data/ — ver
# tiss/data/tuss_22_procedimentos.csv. Substitui/complementa o seed manual
# de 0004_seed_reference_data (27 códigos) pela tabela completa (5.964
# códigos), cobrindo médico e odontológico juntos (mesma tabela ANS, não
# tabelas separadas — distinção por faixa numérica do código).
CSV_PATH = Path(__file__).resolve().parent.parent / 'data' / 'tuss_22_procedimentos.csv'

ODONTO_RANGE = (81000000, 87999999)


def _table_code(tuss_code: str) -> str:
    n = int(tuss_code)
    return '90' if ODONTO_RANGE[0] <= n <= ODONTO_RANGE[1] else '22'


def seed(apps, schema_editor):
    TUSSProcedureCode = apps.get_model('tiss', 'TUSSProcedureCode')

    with open(CSV_PATH, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        to_create = []
        existing_codes = set(TUSSProcedureCode.objects.values_list('tuss_code', flat=True))
        for row in reader:
            tuss_code = row['tuss_code']
            description = row['description']
            table_code = _table_code(tuss_code)
            if tuss_code in existing_codes:
                TUSSProcedureCode.objects.filter(tuss_code=tuss_code).update(
                    description=description, table_code=table_code,
                )
            else:
                to_create.append(TUSSProcedureCode(
                    tuss_code=tuss_code, description=description, table_code=table_code,
                ))
        TUSSProcedureCode.objects.bulk_create(to_create, batch_size=500)


def unseed(apps, schema_editor):
    # Irreversível de forma segura: não temos como saber quais códigos
    # existiam antes do import completo. Não remove nada — apenas no-op.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('tiss', '0004_seed_reference_data'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
