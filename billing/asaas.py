import logging
from django.conf import settings
from django.utils import timezone
import httpx

logger = logging.getLogger(__name__)


class AsaasClient:
    """
    Cliente API para o gateway de pagamentos ASAAS.
    Usa credenciais ASAAS_API_KEY e ASAAS_API_URL do settings.
    Se a chave não estiver configurada, entra em modo MOCK.
    """

    def __init__(self):
        self.api_key = getattr(settings, 'ASAAS_API_KEY', '').strip()
        # Fallback padrão para a URL de Sandbox do ASAAS
        self.api_url = getattr(settings, 'ASAAS_API_URL', 'https://sandbox.asaas.com/api').strip().rstrip('/')
        self.headers = {
            'access_token': self.api_key,
            'Content-Type': 'application/json',
        }
        self.is_mock = not self.api_key or self.api_key == 'mock'

    def create_customer(self, name: str, cnpj_cpf: str, email: str = '', phone: str = '') -> str:
        """
        Cria um cliente no ASAAS. Retorna o ID do cliente criado (ex: cus_...).
        """
        clean_cnpj_cpf = cnpj_cpf.replace('.', '').replace('/', '').replace('-', '').strip()
        if self.is_mock:
            mock_id = f"cus_mock_{clean_cnpj_cpf}"
            logger.info("AsaasClient (MOCK): Criando cliente %s -> %s", name, mock_id)
            return mock_id

        payload = {
            'name': name,
            'cpfCnpj': clean_cnpj_cpf,
            'email': email,
            'phone': phone,
        }

        url = f"{self.api_url}/v3/customers"
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(url, json=payload, headers=self.headers)
                response.raise_for_status()
                data = response.json()
                return data['id']
        except Exception as exc:
            logger.error("AsaasClient: erro ao criar cliente %s. Erro: %s", name, str(exc))
            raise RuntimeError(f"Erro ao criar cliente no ASAAS: {str(exc)}") from exc

    def create_subscription(self, customer_id: str, value: float, billing_type: str = 'PIX', cycle: str = 'MONTHLY') -> str:
        """
        Cria uma assinatura recorrente no ASAAS.
        billing_type: 'PIX', 'BOLETO' ou 'CREDIT_CARD'
        """
        if self.is_mock:
            mock_sub_id = f"sub_mock_{customer_id}"
            logger.info("AsaasClient (MOCK): Criando assinatura para %s -> %s", customer_id, mock_sub_id)
            return mock_sub_id

        next_due = (timezone.now() + timezone.timedelta(days=30)).date().isoformat()
        payload = {
            'customer': customer_id,
            'billingType': billing_type,
            'value': float(value),
            'nextDueDate': next_due,
            'cycle': cycle,
        }

        url = f"{self.api_url}/v3/subscriptions"
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(url, json=payload, headers=self.headers)
                response.raise_for_status()
                data = response.json()
                return data['id']
        except Exception as exc:
            logger.error("AsaasClient: erro ao criar assinatura para cliente %s. Erro: %s", customer_id, str(exc))
            raise RuntimeError(f"Erro ao criar assinatura no ASAAS: {str(exc)}") from exc
