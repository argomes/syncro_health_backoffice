from django.db import migrations


PROCEDURE_CODES = [
    # Médicos (table_code=22) — mesmo seed já usado no Edge Gateway
    # (syncro_gateway/.../migrations/002_tiss00_seed.sql), agora migrado
    # para o backoffice como fonte de verdade única (EDGW-013).
    ('10101012', 'Consulta médica em atenção primária', '22'),
    ('10101039', 'Retorno de consulta médica', '22'),
    ('10101047', 'Consulta médica em pronto-socorro / urgência', '22'),
    ('10102019', 'Consulta em pediatria', '22'),
    ('10104020', 'Consulta em ginecologia', '22'),
    ('10104038', 'Consulta em obstetrícia / pré-natal', '22'),
    ('10105011', 'Consulta em psiquiatria', '22'),
    ('10106018', 'Consulta em cardiologia', '22'),
    ('10108011', 'Consulta em dermatologia', '22'),
    ('10109018', 'Consulta em endocrinologia', '22'),
    ('10110015', 'Consulta em gastroenterologia', '22'),
    ('10112010', 'Consulta em neurologia', '22'),
    ('10114013', 'Consulta em oftalmologia', '22'),
    ('10115010', 'Consulta em ortopedia e traumatologia', '22'),
    ('10116017', 'Consulta em otorrinolaringologia', '22'),
    ('10117014', 'Consulta em pneumologia', '22'),
    ('10118011', 'Consulta em urologia', '22'),
    ('40301361', 'Eletrocardiograma', '22'),
    ('40304361', 'Hemograma completo', '22'),
    ('40601145', 'Raio-X de tórax', '22'),
    # Odontológicos (table_code=90) — confirmados na Tabela TUSS Odontologia
    # oficial (EDGW-013), não fabricados.
    ('81000030', 'Consulta odontológica', '90'),
    ('81000049', 'Consulta odontológica de urgência', '90'),
    ('81000057', 'Consulta odontológica de urgência 24hs', '90'),
    ('81000065', 'Consulta odontológica inicial', '90'),
    ('82000875', 'Exodontia simples de permanente', '90'),
    ('84000198', 'Profilaxia: polimento coronário', '90'),
    ('84000090', 'Aplicação tópica de flúor', '90'),
]

INSURANCE_OPERATORS = [
    # Genéricos (já existiam no Edge Gateway, mesma origem/dados verificados).
    ('305502', 'Unimed', '00111879000120'),
    ('326305', 'Amil', '29309127000179'),
    ('005711', 'Bradesco Saúde', '92693118000160'),
    ('006246', 'SulAmérica', '01685053000151'),
    ('368253', 'Hapvida', '63554067000198'),
    ('359017', 'NotreDame Intermédica', '44649812000138'),
    ('393321', 'Porto Seguro Saúde', '61197445000117'),
    ('417173', 'Prevent Senior', '57952963000186'),
    # Odontológico — Odontoprev é uma entidade nacional única, registro ANS
    # confirmado. Uniodonto NÃO é seedada: é uma federação de cooperativas
    # regionais independentes, cada uma com seu próprio registro ANS (ex:
    # Uniodonto Jacareí 34305-6, Uniodonto Jales 30925-7) — não existe um
    # único "Uniodonto Brasil" nacional. Cadastro da cooperativa específica
    # de cada clínica deve ser feito manualmente via admin.
    ('301949', 'Odontoprev', '58119199000151'),
]


def seed(apps, schema_editor):
    TUSSProcedureCode = apps.get_model('tiss', 'TUSSProcedureCode')
    ANSInsuranceOperator = apps.get_model('tiss', 'ANSInsuranceOperator')

    for tuss_code, description, table_code in PROCEDURE_CODES:
        TUSSProcedureCode.objects.get_or_create(
            tuss_code=tuss_code,
            defaults={'description': description, 'table_code': table_code},
        )
    for ans_code, name, cnpj in INSURANCE_OPERATORS:
        ANSInsuranceOperator.objects.get_or_create(
            ans_code=ans_code,
            defaults={'name': name, 'cnpj': cnpj},
        )


def unseed(apps, schema_editor):
    TUSSProcedureCode = apps.get_model('tiss', 'TUSSProcedureCode')
    ANSInsuranceOperator = apps.get_model('tiss', 'ANSInsuranceOperator')
    TUSSProcedureCode.objects.filter(tuss_code__in=[c[0] for c in PROCEDURE_CODES]).delete()
    ANSInsuranceOperator.objects.filter(ans_code__in=[o[0] for o in INSURANCE_OPERATORS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('tiss', '0003_ansinsuranceoperator_tussprocedurecode'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
