from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    scope = 'login'


class ServiceTokenRateThrottle(AnonRateThrottle):
    scope = 'service_token'


class WebhookRateThrottle(AnonRateThrottle):
    scope = 'webhook'


class LicenseRateThrottle(AnonRateThrottle):
    scope = 'license'
