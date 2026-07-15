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

class ProvisioningClinicDbTemplateTest(TestCase):
    """
    Testes da TASK-053 — CLINIC_DB_TEMPLATE: quando configurado, CREATE DATABASE
    usa TEMPLATE (banco de clínica já nasce com o schema do gateway aplicado).
    """

    def _make_clinic(self, slug='clinica-template-test'):
        """
        Cria a Clinic com o post_save signal (provision_on_key_received)
        REALMENTE desconectado — `@patch('clinics.signals.provision_on_key_received')`
        não funciona aqui porque `@receiver` já registrou o objeto função original
        no dispatcher do Django antes do patch existir (patch só substitui o nome
        no módulo, não o receiver já conectado). Sem isso, `Clinic.objects.create()`
        dispara um provisionamento automático extra, duplicando as chamadas de
        `CREATE DATABASE` e quebrando qualquer asserção de call count.
        """
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

        from django.db.models.signals import post_save
        from clinics.signals import provision_on_key_received

        post_save.disconnect(provision_on_key_received, sender=Clinic)
        try:
            return Clinic.objects.create(
                name='Clínica Template Test', slug=slug, plan=Plan.PROFESSIONAL,
                status=ClinicStatus.ACTIVE, cnpj=f'{uuid.uuid4().hex[:14]}/0001-00',
                public_key_pem=public_pem,
            )
        finally:
            post_save.connect(provision_on_key_received, sender=Clinic)

    @patch('clinics.provisioning.psycopg2.connect')
    def test_create_database_includes_template_when_configured(self, mock_connect):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        clinic = self._make_clinic('clinica-com-template')
        with self.settings(CLINIC_DB_TEMPLATE='clinic_schema_template'):
            provision_clinic_database(clinic)

        calls = [str(c) for c in mock_cursor.execute.call_args_list]
        create_db_calls = [c for c in calls if 'CREATE DATABASE' in c]
        self.assertEqual(len(create_db_calls), 1)
        self.assertIn('TEMPLATE', create_db_calls[0])
        self.assertIn('"clinic_schema_template"', create_db_calls[0])

    @patch('clinics.provisioning.psycopg2.connect')
    def test_create_database_omits_template_when_not_configured(self, mock_connect):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        clinic = self._make_clinic('clinica-sem-template')
        with self.settings(CLINIC_DB_TEMPLATE=''):
            provision_clinic_database(clinic)

        calls = [str(c) for c in mock_cursor.execute.call_args_list]
        create_db_calls = [c for c in calls if 'CREATE DATABASE' in c]
        self.assertEqual(len(create_db_calls), 1)
        self.assertNotIn('TEMPLATE', create_db_calls[0])

    @patch('clinics.provisioning.psycopg2.connect')
    def test_grants_template_owner_role_when_template_configured(self, mock_connect):
        """
        CREATE DATABASE ... TEMPLATE clona os catálogos preservando o dono ORIGINAL
        de cada tabela (clinic_template_owner — ver docker/postgres-init/01-clinic-
        -template.sh), não o usuário recém-criado da clínica. Sem o GRANT logo em
        seguida, o usuário da clínica recebe "permission denied" em toda tabela na
        primeira conexão real (bug encontrado e confirmado manualmente contra o
        Postgres do docker-compose antes desta correção).
        """
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        clinic = self._make_clinic('clinica-grant-owner')
        with self.settings(CLINIC_DB_TEMPLATE='clinic_schema_template'):
            provision_clinic_database(clinic)

        calls = [str(c) for c in mock_cursor.execute.call_args_list]
        grant_calls = [c for c in calls if 'GRANT' in c and 'clinic_template_owner' in c]
        self.assertEqual(len(grant_calls), 1)
        # GRANT precisa vir depois do CREATE DATABASE (a role só existe pro
        # usuário se conectar após o banco existir; a ordem também documenta a
        # intenção — não é apenas side-effect de qualquer GRANT solto).
        create_idx = next(i for i, c in enumerate(calls) if 'CREATE DATABASE' in c)
        grant_idx = next(i for i, c in enumerate(calls) if 'GRANT' in c and 'clinic_template_owner' in c)
        self.assertGreater(grant_idx, create_idx)

    @patch('clinics.provisioning.psycopg2.connect')
    def test_no_template_owner_grant_when_template_not_configured(self, mock_connect):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        clinic = self._make_clinic('clinica-sem-grant')
        with self.settings(CLINIC_DB_TEMPLATE=''):
            provision_clinic_database(clinic)

        calls = [str(c) for c in mock_cursor.execute.call_args_list]
        grant_calls = [c for c in calls if 'clinic_template_owner' in c]
        self.assertEqual(len(grant_calls), 0)

    @patch('clinics.provisioning.psycopg2.connect')
    def test_invalid_template_name_raises_before_touching_db(self, mock_connect):
        """Nome de template malicioso/inválido nunca deve chegar ao DDL bruto."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        clinic = self._make_clinic('clinica-template-invalido')
        with self.settings(CLINIC_DB_TEMPLATE='"; DROP DATABASE postgres; --'):
            with self.assertRaises(RuntimeError):
                provision_clinic_database(clinic)
