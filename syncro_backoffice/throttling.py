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


class ErrorReportRateThrottle(AnonRateThrottle):
    # EDGW-052 (achado de segurança na revisão, 2026-07-27): mesmo padrão de
    # falha já documentado em DbAccessGrantRateThrottle — IsAuthenticatedByLicenseKey
    # é permission, não authentication, então sem scope dedicado o endpoint
    # herdava só o AnonRateThrottle default (60/min por IP). Uma license_key
    # vazada poderia inundar o Zoho Desk de tickets falsos. Rate baixa porque
    # reportar problema é ação rara e legítima em volume baixo.
    scope = 'error_report'


class ReferenceDataRateThrottle(AnonRateThrottle):
    # BACFF-AVULSA-03 (correção 2026-07-20): os 4 endpoints de referência
    # TUSS/ANS (procedure_code_lookup, insurance_operator_lookup,
    # procedure_code_search, insurance_operator_search) usam apenas
    # IsAuthenticatedByLicenseKey (permission, não authentication) e
    # herdavam só o AnonRateThrottle default (60/min por IP) — insuficiente
    # para diferenciar uso legítimo do gateway (cache-aside) de scraping
    # fatiado da tabela pública TUSS/ANS. Dado é público; o risco aqui é
    # carga/disponibilidade, não vazamento.
    scope = 'reference_data'
