from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.utils import timezone
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
import uuid

from clinics.models import Clinic, ClinicStatus, ProvisioningStatus, Plan
from clinics.provisioning import provision_clinic_database, encrypt_with_public_key, _validate_pg_identifier


class ProvisioningFunctionsTest(TestCase):
    """Testes de funções de provisioning"""

    @patch('clinics.signals.provision_on_key_received')
    def setUp(self, mock_signal):
        # Gerar chave RSA válida para testes
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key()
        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()

        self.clinic = Clinic.objects.create(
            name='Clínica Provision',
            slug='clinica-provision',
            plan=Plan.PROFESSIONAL,
            status=ClinicStatus.ACTIVE,
            cnpj='12.345.678/0001-99',
            public_key_pem=public_pem,
        )

    @patch('clinics.provisioning.psycopg2.connect')
    def test_provision_clinic_database_success(self, mock_connect):
        """Provisionar banco de dados com sucesso"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        result = provision_clinic_database(self.clinic)

        # Validar que retorna tupla (db_name, db_user, encrypted_password)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 3)
        db_name, db_user, encrypted_password = result

        self.assertIsNotNone(db_name)
        self.assertIsNotNone(db_user)
        self.assertIsNotNone(encrypted_password)

        # Validar chamadas ao banco
        mock_cursor.execute.assert_called()

    @patch('clinics.provisioning.psycopg2.connect')
    def test_provision_clinic_database_connection_error(self, mock_connect):
        """Erro ao conectar no PostgreSQL"""
        mock_connect.side_effect = Exception('Connection refused')

        with self.assertRaises(Exception):
            provision_clinic_database(self.clinic)

    @patch('clinics.provisioning.psycopg2.connect')
    def test_provision_clinic_database_creates_user(self, mock_connect):
        """Provisioning cria usuário no PostgreSQL"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        provision_clinic_database(self.clinic)

        # Validar que CREATE USER foi executado
        calls_str = str(mock_cursor.execute.call_args_list)
        self.assertIn('CREATE', calls_str)

    def test_encrypt_with_public_key_success(self):
        """Encriptar senha com chave pública"""
        # Usar uma chave de teste real
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key()
        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()

        plaintext = 'minha_senha_secreta'

        # Encriptar
        encrypted = encrypt_with_public_key(public_pem, plaintext)

        # Validar que é base64
        import base64
        try:
            decoded = base64.b64decode(encrypted)
            self.assertTrue(len(decoded) > 0)
        except Exception:
            self.fail('Encrypted result is not valid base64')

    def test_encrypt_with_public_key_invalid_key(self):
        """Erro com chave pública inválida"""
        invalid_key = 'not-a-valid-key'
        plaintext = 'senha'

        with self.assertRaises(Exception):
            encrypt_with_public_key(invalid_key, plaintext)


class DDLSanitizationTest(TestCase):
    """Testes de sanitização de identificadores PostgreSQL (BO-004)"""

    def test_valid_identifier_passes(self):
        self.assertEqual(_validate_pg_identifier('clinic_abc123'), 'clinic_abc123')
        self.assertEqual(_validate_pg_identifier('u_minha_clinica'), 'u_minha_clinica')

    def test_sql_injection_via_semicolon_raises(self):
        with self.assertRaises(ValueError):
            _validate_pg_identifier('abc; DROP DATABASE postgres; --')

    def test_sql_injection_via_quotes_raises(self):
        with self.assertRaises(ValueError):
            _validate_pg_identifier('abc" OR "1"="1')

    def test_identifier_starting_with_digit_raises(self):
        with self.assertRaises(ValueError):
            _validate_pg_identifier('1clinic')

    def test_identifier_with_uppercase_raises(self):
        with self.assertRaises(ValueError):
            _validate_pg_identifier('Clinic_ABC')

    def test_identifier_with_hyphen_raises(self):
        # slugs com hífen devem ser convertidos antes de chegar aqui
        with self.assertRaises(ValueError):
            _validate_pg_identifier('clinic-abc')

    def test_empty_identifier_raises(self):
        with self.assertRaises(ValueError):
            _validate_pg_identifier('')

    def test_identifier_too_long_raises(self):
        # 64 chars — acima do limite de 63
        with self.assertRaises(ValueError):
            _validate_pg_identifier('a' * 64)

    def test_identifier_max_length_passes(self):
        # 63 chars — no limite
        _validate_pg_identifier('a' + 'b' * 62)

    @patch('clinics.provisioning.psycopg2.connect')
    def test_provision_uses_quoted_identifiers(self, mock_connect):
        """DDL deve usar identificadores entre aspas duplas, não interpolação direta."""
        from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
        from cryptography.hazmat.primitives import serialization as _ser

        private_key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_pem = private_key.public_key().public_bytes(
            encoding=_ser.Encoding.PEM,
            format=_ser.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

        from clinics.models import Clinic, ClinicStatus, Plan
        clinic = Clinic(name='Test', slug='test-clinic', plan=Plan.PROFESSIONAL,
                        status=ClinicStatus.ACTIVE, public_key_pem=public_pem)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        provision_clinic_database(clinic)

        calls = [str(c) for c in mock_cursor.execute.call_args_list]
        for call in calls:
            if 'CREATE USER' in call or 'CREATE DATABASE' in call or 'REVOKE' in call:
                self.assertIn('"', call, "Identificador deve estar entre aspas duplas no DDL")