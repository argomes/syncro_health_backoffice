"""
TASK-BO-R01 — reaplica clinics/sql/clinic_schema/ em toda clínica já
provisionada.

Gap real encontrado ao implementar a sync de appointment_payments:
`clinics/provisioning.py::_apply_clinic_schema` só roda uma vez, no momento
do CREATE DATABASE de uma clínica NOVA (provision_clinic_database). Uma
migration nova em clinic_schema/ (como 011_appointment_payments.sql) nunca
chega ao banco de uma clínica que já existia antes dela — mesma classe de
problema já documentada (schema cloud ficou 41 migrations atrasado até
2026-08-19). Sem este comando, a Ambar (primeiro cliente real) nunca
receberia a tabela/função novas.

Seguro rodar em produção: todo arquivo em clinic_schema/ é idempotente
(CREATE TABLE IF NOT EXISTS / CREATE OR REPLACE FUNCTION), então reaplicar o
conjunto inteiro em uma clínica já atualizada não tem efeito — não há
tracking arquivo-a-arquivo de "o que já rodou", de propósito (framework de
migration incremental seria overengineering pra este caso, regra 2.2/2.3 do
CLAUDE.md: todo arquivo já é seguro de rodar de novo).
"""
import logging

from django.core.management.base import BaseCommand

from clinics.models import Clinic
from clinics.provisioning import _apply_clinic_schema

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Reaplica clinics/sql/clinic_schema/ (idempotente) em toda clínica já '
        'provisionada — usar sempre que uma migration nova for adicionada ao '
        'diretório, para que clínicas existentes recebam o schema novo.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--clinic-slug',
            default=None,
            help='Limita a uma única clínica (por slug). Sem isso, roda em todas as provisionadas.',
        )

    def handle(self, *args, **options):
        qs = Clinic.objects.exclude(db_name='').exclude(db_user='')
        slug = options.get('clinic_slug')
        if slug:
            qs = qs.filter(slug=slug)

        clinics = list(qs)
        if not clinics:
            self.stdout.write(self.style.WARNING('Nenhuma clínica provisionada encontrada.'))
            return

        ok, failed = 0, 0
        for clinic in clinics:
            try:
                _apply_clinic_schema(clinic.db_name, clinic.db_user)
                ok += 1
                self.stdout.write(self.style.SUCCESS(f'OK  — {clinic.slug}'))
            except Exception as exc:  # noqa: BLE001 — segue para as outras clínicas
                failed += 1
                # Nunca logar detalhe de exceção que possa carregar dado de
                # conexão/credencial — só o tipo da exceção (regra LGPD 4.1).
                logger.error('sync_clinic_schema_failed clinic=%s error_type=%s', clinic.slug, type(exc).__name__)
                self.stdout.write(self.style.ERROR(f'FALHOU — {clinic.slug} ({type(exc).__name__})'))

        self.stdout.write(f'\nTotal: {ok} ok, {failed} falharam, {len(clinics)} clínicas.')
