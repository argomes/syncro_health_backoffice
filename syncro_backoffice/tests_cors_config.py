from django.test import SimpleTestCase
from django.conf import settings


class CorsConfigTest(SimpleTestCase):
    """
    BACFF-007: CORS_ALLOW_ALL_ORIGINS não pode depender de DEBUG. Se DEBUG=True
    vazasse para staging/produção (erro comum de .env mal gerenciado), CORS
    ficaria totalmente aberto, permitindo requisição cross-origin autenticada
    usando as credenciais do navegador do admin logado.
    """

    def test_cors_allow_all_origins_is_always_false(self):
        self.assertFalse(settings.CORS_ALLOW_ALL_ORIGINS)

    def test_cors_allow_all_origins_is_not_coupled_to_debug(self):
        # Independente do valor de DEBUG neste ambiente, CORS_ALLOW_ALL_ORIGINS
        # deve continuar False — não é `== settings.DEBUG`.
        self.assertIs(settings.CORS_ALLOW_ALL_ORIGINS, False)
