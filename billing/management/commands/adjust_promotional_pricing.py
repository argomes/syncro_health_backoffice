from django.core.management.base import BaseCommand

from billing.services import adjust_promotional_pricing


class Command(BaseCommand):
    help = (
        'TASK-BO-11: reajusta pra R$199,90 as clínicas com desconto de '
        'lançamento que completaram 12 meses e não têm desconto de '
        'fidelidade marcado. Idempotente — rodar diariamente (cron/Celery Beat).'
    )

    def handle(self, *args, **options):
        result = adjust_promotional_pricing()
        self.stdout.write(self.style.SUCCESS(
            f"Reajuste concluído: {result['adjusted']} reajustada(s), "
            f"{result['kept_loyalty']} mantida(s) por fidelidade, "
            f"{result['errors']} erro(s)."
        ))
