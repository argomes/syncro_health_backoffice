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
            # BACFF-009 (LGPD): nunca logar nome/CPF-CNPJ do cliente — nem o
            # mock_id, que embute o documento limpo (usado como retorno pro
            # chamador, não deve vazar para o log).
            logger.info("AsaasClient (MOCK): cliente criado com sucesso")
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
            # BACFF-009 (LGPD): nunca logar nome/CPF-CNPJ do cliente — só o
            # detalhe técnico da falha (resposta da API, timeout, etc.).
            logger.error("AsaasClient: erro ao criar cliente no ASAAS. Erro: %s", str(exc))
            raise RuntimeError(f"Erro ao criar cliente no ASAAS: {str(exc)}") from exc

    def create_subscription(
        self,
        customer_id: str,
        value: float,
        billing_type: str = 'PIX',
        cycle: str = 'MONTHLY',
        description: str = '',
        external_reference: str = '',
    ) -> str:
        """
        Cria uma assinatura recorrente no ASAAS.
        billing_type: 'PIX', 'BOLETO' ou 'CREDIT_CARD'

        `description` deve ser sempre um texto genérico de plano (ex: "Plano
        Syncro Health — mensalidade") — nunca deve conter dado de paciente
        ou informação identificável específica além do necessário. LGPD:
        nenhum campo de paciente é enviado ao Asaas.
        `external_reference` deve ser o UUID da Clinic (não nome em texto
        livre), usado para correlação sem vazar dado sensível.
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
        if description:
            payload['description'] = description
        if external_reference:
            payload['externalReference'] = external_reference

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

    def update_subscription_value(self, subscription_id: str, new_value: float) -> bool:
        """
        Atualiza o valor de uma assinatura existente no ASAAS.
        PUT /v3/subscriptions/{id}

        O Asaas não reajusta assinaturas automaticamente — cobra sempre o
        mesmo `value` até esse endpoint ser chamado. Usado pelo job de
        reajuste de preço (fim do desconto de lançamento).
        """
        if self.is_mock:
            logger.info(
                "AsaasClient (MOCK): Atualizando assinatura %s -> value=%s",
                subscription_id,
                new_value,
            )
            return True

        payload = {'value': float(new_value)}
        url = f"{self.api_url}/v3/subscriptions/{subscription_id}"
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.put(url, json=payload, headers=self.headers)
                response.raise_for_status()
                return True
        except Exception as exc:
            logger.error(
                "AsaasClient: erro ao atualizar assinatura %s. Erro: %s",
                subscription_id,
                str(exc),
            )
            raise RuntimeError(f"Erro ao atualizar assinatura no ASAAS: {str(exc)}") from exc
