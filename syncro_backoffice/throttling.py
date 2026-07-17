from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    scope = 'login'


class ServiceTokenRateThrottle(AnonRateThrottle):
    scope = 'service_token'


class WebhookRateThrottle(AnonRateThrottle):
    scope = 'webhook'


class LicenseRateThrottle(AnonRateThrottle):
    scope = 'license'


class DbAccessGrantRateThrottle(AnonRateThrottle):
    # BACFF-AVULSA-06 (correção 2026-07-17): db-access-grant grava uma senha
    # de banco Postgres válida em cache. IsAuthenticatedByLicenseKey é uma
    # *permission*, não uma *authentication* class — o DRF trata a requisição
    # como anônima, então sem throttle_scope dedicado ela herdava apenas o
    # AnonRateThrottle default (60/min), insuficiente para uma ação
    # administrativa sensível e rara. Rate própria e baixa em settings.py.
    scope = 'db_access_grant'
