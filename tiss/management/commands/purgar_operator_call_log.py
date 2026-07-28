"""
§8.7 do documento de arquitetura — retenção do `OperatorCallLog`.

A tabela é append-only e recebe uma linha por chamada de negócio a uma
operadora. Sem purga ela cresce sem limite e vira custo de banco no Railway.
90 dias é bem mais que a janela de qualquer pergunta que o dashboard de
saúde faz (15 min a 24 h) e cobre análise de tendência trimestral.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from tiss.models import OperatorCallLog

RETENCAO_PADRAO_DIAS = 90


class Command(BaseCommand):
    help = 'Purga registros de OperatorCallLog mais antigos que N dias (padrão: 90).'

    def add_arguments(self, parser):
        parser.add_argument('--dias', type=int, default=RETENCAO_PADRAO_DIAS)
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Só conta o que seria apagado, sem apagar.',
        )

    def handle(self, *args, **options):
        dias = options['dias']
        if dias < 1:
            self.stderr.write('--dias precisa ser >= 1')
            return

        corte = timezone.now() - timedelta(days=dias)
        qs = OperatorCallLog.objects.filter(created_at__lt=corte)
        total = qs.count()

        if options['dry_run']:
            self.stdout.write(f'[dry-run] {total} registro(s) anteriores a {corte.date()} seriam apagados.')
            return

        qs.delete()
        self.stdout.write(f'{total} registro(s) de OperatorCallLog anteriores a {corte.date()} apagados.')
