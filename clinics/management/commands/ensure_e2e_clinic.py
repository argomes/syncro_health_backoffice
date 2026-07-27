"""
CROSS-017.1 — garante (idempotente) a existência de uma clínica fixa usada
pela suite Playwright de "sync real" (playwright.config.sync.ts /
e2e/specs-sync/*.spec.ts), que roda contra Postgres+Redis via Docker (NÃO a
suite padrão isolada/SQLite do CROSS-004).

get_or_create por slug: seguro rodar em todo `npm run test:e2e:sync` sem
duplicar clínica.

O gateway roda em TEST_MODE com SQLite `:memory:` (seed_e2e.go), então gera um
par de chaves RSA novo a cada boot — a chave pública registrada numa rodada
anterior fica obsoleta assim que o processo do gateway morre. Por isso,
diferente de uma clínica real, esta clínica de teste tem `provisioning_status`
e `public_key_pem` resetados para `waiting_key`/vazio a cada execução deste
comando, forçando o gateway a re-registrar sua chave pública atual no boot
seguinte (`registerPublicKeyBestEffort`, só dispara quando status ==
"waiting_key"). Sem isso, `bootstrapCloudWithLicense` falha ao descriptografar
as credenciais do Postgres com a chave privada nova contra a pública antiga.
"""
import random
import uuid

from django.core.management.base import BaseCommand

from clinics.models import Clinic

E2E_SYNC_SLUG = 'clinic-e2e-sync'
E2E_SYNC_LICENSE_KEY = uuid.UUID('e2e00000-0000-0000-0000-000000000001')


def _unused_fictitious_cnpj() -> str:
    # O valor em si é irrelevante para os specs de sync (a clínica é
    # localizada por slug, não por CNPJ) — só precisa ser único no SQLite
    # local do backoffice, que acumula clínicas de outras rodadas manuais.
    while True:
        candidate = f'{random.randint(10**13, 10**14 - 1)}'
        if not Clinic.objects.filter(cnpj=candidate).exists():
            return candidate


class Command(BaseCommand):
    help = 'Garante (idempotente) a clínica fixa usada pela suíte E2E de sync real (CROSS-017.1).'

    def handle(self, *args, **options):
        clinic, created = Clinic.objects.get_or_create(
            slug=E2E_SYNC_SLUG,
            defaults={
                'name': 'Clínica E2E Sync (CROSS-017.1)',
                'license_key': E2E_SYNC_LICENSE_KEY,
                'cnpj': _unused_fictitious_cnpj(),
                'active_modules': ['billing', 'tiss'],
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(
                f'Clínica E2E de sync criada: slug={clinic.slug} license_key={clinic.license_key}'
            ))
        else:
            clinic.provisioning_status = 'waiting_key'
            clinic.public_key_pem = ''
            clinic.save(update_fields=['provisioning_status', 'public_key_pem'])
            self.stdout.write(
                f'Clínica E2E de sync já existia (reaproveitada): slug={clinic.slug} — '
                f'provisioning_status resetado para waiting_key (chave RSA do gateway é nova a cada boot em TEST_MODE) '
                f'license_key={clinic.license_key}'
            )
