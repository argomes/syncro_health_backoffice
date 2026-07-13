"""
Decriptação bit-compatível com o esquema AES-256-GCM usado pelo gateway (Go,
syncro_gateway/pkg/crypto/crypto.go) para os campos do dual envelope
(TASK-039/040/041). Formato confirmado por leitura direta do código Go:

- Chave: 32 bytes (AES-256).
- Ciphertext em disco = base64 padrão (com padding) de `nonce(12 bytes) || ct+tag(16 bytes)`.
  O nonce vai PRIMEIRO — não é sufixo.
- GCM sem AAD (`nil` no lado Go — associated_data=None aqui).
- `dek = Decrypt(dek_encrypted_session, key=temp_key)`, depois `campo = Decrypt(campo_enc, key=dek)`.
  Sem KDF/HKDF entre as etapas — é só GCM duas vezes, chaves diferentes.
"""
import base64
import hashlib

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_SIZE = 12


def session_key_id(temp_key: bytes) -> str:
    """
    Espelha syncro_gateway/internal/adapters/output/cloud/strategy_helpers.go::sessionKeyID:
    primeiros 16 hex chars (8 bytes) de SHA-256(TemporaryKey). Usado para filtrar,
    na leitura, só os registros que o gateway de fato cifrou com ESTA sessão —
    não com uma sessão anterior cujo dek_encrypted_session ainda esteja na coluna.
    """
    return hashlib.sha256(temp_key).hexdigest()[:16]


class DecryptionError(Exception):
    """Ciphertext corrompido, chave errada, ou formato inesperado."""


def decrypt_field(ciphertext_b64: str, key: bytes) -> bytes:
    """Decripta um campo cifrado pelo gateway. Retorna b'' para campo vazio/None
    (mesmo comportamento do Go: `clinical_notes_enc` fica vazio quando o campo
    original era vazio, não é erro)."""
    if not ciphertext_b64:
        return b''
    if len(key) != 32:
        raise DecryptionError(f'chave precisa ter 32 bytes, tem {len(key)}')

    try:
        blob = base64.b64decode(ciphertext_b64, validate=True)
    except Exception as exc:
        raise DecryptionError('base64 inválido') from exc

    if len(blob) < NONCE_SIZE:
        raise DecryptionError('ciphertext menor que o nonce — formato inválido')

    nonce, ct = blob[:NONCE_SIZE], blob[NONCE_SIZE:]
    try:
        return AESGCM(key).decrypt(nonce, ct, None)
    except InvalidTag as exc:
        raise DecryptionError('tag GCM inválida — chave errada ou dado corrompido') from exc


def decrypt_dek(dek_encrypted_session_b64: str, temp_key: bytes) -> bytes:
    dek = decrypt_field(dek_encrypted_session_b64, temp_key)
    if len(dek) != 32:
        raise DecryptionError(f'DEK decriptada com tamanho inesperado: {len(dek)} bytes')
    return dek


def decrypt_field_str(ciphertext_b64: str, dek: bytes) -> str:
    return decrypt_field(ciphertext_b64, dek).decode('utf-8')
