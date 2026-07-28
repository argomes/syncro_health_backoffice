from django.apps import AppConfig


class TissConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tiss'
    # Sem nome de operadora aqui: o app fala com N operadoras via
    # `tiss/providers/`, e a Orizon é só a primeira integrada. Rotular o
    # módulo inteiro com uma operadora é o tipo de hardcode que o lint
    # arquitetural (§8.4, tests_providers.py) existe para pegar.
    verbose_name = 'TISS/SOAP'
