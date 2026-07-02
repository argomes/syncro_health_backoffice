"""
Teste de integração: ciclo completo de provisionamento de clínica.

Simula o que acontece desde a criação da clínica no Backoffice
até o Edge Gateway descriptografar a senha do PostgreSQL.

Não requer PostgreSQL real — mockamos apenas provision_clinic_database.
Executa com:
    python manage.py test clinics.tests_e2e_license_flow --verbosity=2
"""

import base64
import uuid
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status as http_status

from .models import Clinic, ClinicStatus, Plan, ProvisioningStatus
from .provisioning import encrypt_with_public_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_rsa_keypair():
    """Gera par RSA-2048. Retorna (private_key_pem, public_key_pem) como strings."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    return private_pem, public_pem


def _rsa_decrypt(ciphertext_b64: str, private_key_pem: str) -> str:
    """Descriptografa com RSA-OAEP-SHA256. Simula o que o Edge Go faz."""
    private_key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    ciphertext = base64.b64decode(ciphertext_b64)
    plaintext = private_key.decrypt(
        ciphertext,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return plaintext.decode()


def _make_clinic_waiting_key(name='Clínica Teste E2E'):
    """Cria clínica ativa no estado WAITING_KEY (aguarda chave pública do Edge)."""
    return Clinic.objects.create(
        name=name,
        slug=f'e2e-{uuid.uuid4().hex[:8]}',
        plan=Plan.PROFESSIONAL,
        status=ClinicStatus.ACTIVE,
        cnpj='12.345.678/0001-99',
        provisioning_status=ProvisioningStatus.WAITING_KEY,
    )


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------

class LicenseFlowE2ETest(TestCase):
    """
    Ciclo completo: Edge gera RSA → envia public key → Backoffice provisiona →
    Edge busca credenciais → Edge descriptografa senha → conectaria ao PG.
    """

    def setUp(self):
        self.client = APIClient()

    def test_full_provisioning_flow(self):
        """
        Fluxo feliz completo:
        1. Clínica criada no Backoffice (status WAITING_KEY)
        2. Edge gera par RSA e envia public key via POST /register-key/
        3. Backoffice "provisiona" (mockado) e salva credenciais cifradas
        4. Edge valida licença — retorna active + provisioned
        5. Edge busca credenciais via GET /credentials/
        6. Edge descriptografa a senha com sua chave privada
        """
        # PASSO 1: Admin cria clínica no Backoffice (simulado criando direto no DB)
        clinic = _make_clinic_waiting_key('Hospital São Lucas')

        # PASSO 2: Edge Gateway gera par RSA (feito no setup wizard)
        private_pem, public_pem = _generate_rsa_keypair()

        # Mock da criação do banco PostgreSQL real
        fake_password = 'super_secret_pg_password_42'
        fake_db_name = f'clinic_{clinic.slug.replace("-", "_")}'
        fake_db_user = f'u_{clinic.slug.replace("-", "_")}'

        def mock_provision(clinic_instance):
            encrypted = encrypt_with_public_key(clinic_instance.public_key_pem, fake_password)
            return fake_db_name, fake_db_user, encrypted

        with patch('clinics.signals.provision_clinic_database', side_effect=mock_provision):
            # PASSO 3: Edge envia chave pública ao Backoffice
            response = self.client.post(
                '/api/clinics/register-key/',
                {'public_key_pem': public_pem},
                format='json',
                HTTP_X_LICENSE_KEY=str(clinic.license_key),
            )
            self.assertEqual(response.status_code, http_status.HTTP_200_OK)
            self.assertEqual(response.data['provisioning_status'], ProvisioningStatus.PROVISIONED)

        # PASSO 4: Edge valida licença
        response = self.client.post(
            '/api/clinics/validate-license/',
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.data['status'], ClinicStatus.ACTIVE)
        self.assertEqual(response.data['provisioning_status'], ProvisioningStatus.PROVISIONED)

        # PASSO 5: Edge busca credenciais cifradas
        response = self.client.get(
            '/api/clinics/credentials/',
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.data['db_name'], fake_db_name)
        self.assertEqual(response.data['db_user'], fake_db_user)
        self.assertIn('db_password_encrypted', response.data)
        encrypted_password = response.data['db_password_encrypted']

        # PASSO 6: Edge descriptografa com sua chave privada (o que Go faz com DecryptRSAOAEP)
        decrypted_password = _rsa_decrypt(encrypted_password, private_pem)
        self.assertEqual(decrypted_password, fake_password)

        # Confirma que a senha nunca apareceu em plaintext na API
        self.assertNotIn(fake_password, str(response.data))

    def test_credentials_before_provisioning_returns_424(self):
        """Edge que ainda não enviou public key recebe 424 ao buscar credenciais."""
        clinic = _make_clinic_waiting_key('Clínica Sem Provisão')

        response = self.client.get(
            '/api/clinics/credentials/',
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )
        self.assertEqual(response.status_code, http_status.HTTP_424_FAILED_DEPENDENCY)

    def test_register_key_idempotency_blocks_second_call(self):
        """
        Clínica já provisionada não aceita novo envio de public key.
        Evita sobrescrever credenciais de uma clínica em produção.
        """
        clinic = _make_clinic_waiting_key('Clínica Já Provisionada')
        _, public_pem = _generate_rsa_keypair()

        def mock_provision(clinic_instance):
            return 'db_x', 'user_x', encrypt_with_public_key(clinic_instance.public_key_pem, 'senha')

        with patch('clinics.signals.provision_clinic_database', side_effect=mock_provision):
            # Primeiro envio — ok
            r1 = self.client.post(
                '/api/clinics/register-key/',
                {'public_key_pem': public_pem},
                format='json',
                HTTP_X_LICENSE_KEY=str(clinic.license_key),
            )
            self.assertEqual(r1.status_code, http_status.HTTP_200_OK)

        # Segundo envio — deve ser bloqueado
        _, public_pem2 = _generate_rsa_keypair()
        r2 = self.client.post(
            '/api/clinics/register-key/',
            {'public_key_pem': public_pem2},
            format='json',
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )
        self.assertEqual(r2.status_code, http_status.HTTP_409_CONFLICT)
        self.assertEqual(r2.data['error'], 'already_provisioned')

    def test_suspended_clinic_cannot_get_credentials(self):
        """Clínica suspensa não consegue buscar credenciais — licença inativa."""
        clinic = Clinic.objects.create(
            name='Clínica Suspensa',
            slug=f'suspensa-{uuid.uuid4().hex[:8]}',
            plan=Plan.PROFESSIONAL,
            status=ClinicStatus.SUSPENDED,
            cnpj='99.999.999/0001-99',
            provisioning_status=ProvisioningStatus.PROVISIONED,
            db_name='clinic_suspensa',
            db_user='u_suspensa',
            db_password_encrypted='qualquer',
        )

        response = self.client.get(
            '/api/clinics/credentials/',
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )
        # IsAuthenticatedByLicenseKey só permite ACTIVE — retorna 4xx
        self.assertIn(response.status_code, (
            http_status.HTTP_401_UNAUTHORIZED,
            http_status.HTTP_403_FORBIDDEN,
        ))

    def test_validate_license_returns_correct_plan_and_clinic_name(self):
        """validate-license expõe metadados da clínica para o Edge mostrar no UI."""
        clinic = _make_clinic_waiting_key('Clínica Esperança')

        response = self.client.post(
            '/api/clinics/validate-license/',
            HTTP_X_LICENSE_KEY=str(clinic.license_key),
        )
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.data['clinic_name'], 'Clínica Esperança')
        self.assertEqual(response.data['plan'], Plan.PROFESSIONAL)
        self.assertEqual(response.data['provisioning_status'], ProvisioningStatus.WAITING_KEY)


class EncryptDecryptCompatibilityTest(TestCase):
    """
    Verifica compatibilidade RSA Python (Backoffice) ↔ Go (Edge).
    O Go usa rsa.DecryptOAEP(sha256, rand, key, ciphertext, nil) — mesma spec.
    """

    def test_encrypt_python_decrypt_python(self):
        """Smoke test: encrypt_with_public_key + _rsa_decrypt são compatíveis."""
        private_pem, public_pem = _generate_rsa_keypair()
        plaintext = 'minha_senha_super_secreta_123!'

        encrypted = encrypt_with_public_key(public_pem, plaintext)
        decrypted = _rsa_decrypt(encrypted, private_pem)

        self.assertEqual(decrypted, plaintext)

    def test_wrong_private_key_cannot_decrypt(self):
        """Chave privada errada não consegue descriptografar."""
        _, public_pem = _generate_rsa_keypair()
        wrong_private_pem, _ = _generate_rsa_keypair()

        encrypted = encrypt_with_public_key(public_pem, 'segredo')

        with self.assertRaises(Exception):
            _rsa_decrypt(encrypted, wrong_private_pem)

    def test_encrypted_output_is_valid_base64(self):
        """A senha cifrada deve ser base64 puro — seguro para JSON/HTTP."""
        _, public_pem = _generate_rsa_keypair()
        encrypted = encrypt_with_public_key(public_pem, 'senha_teste')

        # Não deve lançar exceção
        decoded = base64.b64decode(encrypted)
        self.assertEqual(len(decoded), 256)  # RSA-2048 → 256 bytes

    def test_each_encryption_produces_different_ciphertext(self):
        """OAEP usa padding aleatório — mesmo plaintext gera ciphertext diferente a cada vez."""
        _, public_pem = _generate_rsa_keypair()
        plaintext = 'mesma_senha'

        encrypted1 = encrypt_with_public_key(public_pem, plaintext)
        encrypted2 = encrypt_with_public_key(public_pem, plaintext)

        self.assertNotEqual(encrypted1, encrypted2)
