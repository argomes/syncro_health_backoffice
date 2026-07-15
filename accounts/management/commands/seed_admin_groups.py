"""
TASK-056 — cria/atualiza os grupos de acesso ao Django Admin (Unfold).

Idempotente: pode rodar em todo deploy sem duplicar grupos nem perder
customizações feitas manualmente em outros grupos (só mexe nos dois
grupos que ele conhece).

Uso: python manage.py seed_admin_groups
"""
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db.models import Q


# Mapeamento (app_label, model) -> lista de codenames de permissão.
# 'view' cobre leitura no changelist/detail; 'add'/'change'/'delete' são
# as ações padrão do Django admin.
ANALISTA_OPERACIONAL_PERMS = {
    ('clinics', 'clinic'): ['view'],
    ('metrics', 'systemheartbeat'): ['view'],
    ('metrics', 'systemlog'): ['view'],
    ('portal_gestor', 'reportsession'): ['view'],
    ('support', 'ticket'): ['view', 'add', 'change'],
    ('support', 'ticketmessage'): ['view', 'add', 'change'],
}

FINANCEIRO_PERMS = {
    ('clinics', 'clinic'): ['view'],
    # Modelo de assinatura ASAAS ainda não existe — adicionar aqui quando
    # a integração for implementada (ver TASK-056 no repo SyncroHealth).
}

GROUPS = {
    'Analista Operacional': ANALISTA_OPERACIONAL_PERMS,
    'Financeiro': FINANCEIRO_PERMS,
}


class Command(BaseCommand):
    help = 'Cria/atualiza os grupos de acesso ao admin (Analista Operacional, Financeiro) — TASK-056'

    def handle(self, *args, **options):
        for group_name, perm_map in GROUPS.items():
            group, created = Group.objects.get_or_create(name=group_name)

            perm_query = Q()
            for (app_label, model), codenames in perm_map.items():
                full_codenames = [f'{action}_{model}' for action in codenames]
                perm_query |= Q(content_type__app_label=app_label, codename__in=full_codenames)

            if perm_map:
                permissions = Permission.objects.filter(perm_query)
            else:
                permissions = Permission.objects.none()

            group.permissions.set(permissions)

            action = 'criado' if created else 'atualizado'
            self.stdout.write(self.style.SUCCESS(
                f'Grupo "{group_name}" {action} — {permissions.count()} permissões aplicadas.'
            ))
