"""
EDGW-044 (Fase 1) — purga de backups S3 além da retenção LGPD (Art. 16,
cumprimento de obrigação legal). Roda por cron externo (mesmo padrão de
`tiss/management/commands/purgar_operator_call_log.py`) — sem Celery beat
configurado neste projeto.

A listagem/deleção S3 usa credenciais reais, por isso vive só aqui
(backoffice) e nunca no gateway — mesmo princípio de "conhecimento zero"
das credenciais AWS aplicado ao restante do pipeline de backup.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from clinics.models import Clinic, ClinicStatus

from backup.services import purge_old_backups


class Command(BaseCommand):
    help = 'Purga backups S3 mais antigos que N dias, para todas as clínicas ativas (padrão: BACKUP_RETENTION_DAYS).'

    def add_arguments(self, parser):
        parser.add_argument('--dias', type=int, default=None)

    def handle(self, *args, **options):
        dias = options['dias'] or settings.BACKUP_RETENTION_DAYS
        if dias < 1:
            self.stderr.write('--dias precisa ser >= 1')
            return

        total_deletado = 0
        clinicas = Clinic.objects.filter(status=ClinicStatus.ACTIVE)
        for clinic in clinicas:
            deletado = purge_old_backups(clinic, dias)
            if deletado:
                self.stdout.write(f'clinic_id={clinic.id}: {deletado} backup(s) removido(s) (>{dias} dias)')
            total_deletado += deletado

        self.stdout.write(self.style.SUCCESS(
            f'Purga concluída: {total_deletado} backup(s) removido(s) em {clinicas.count()} clínica(s) ativa(s).'
        ))
