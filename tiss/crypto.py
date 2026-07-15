"""
Criptografia em repouso das credenciais de operadora (login/senha) armazenadas
em TISSOperatorConfig. Usa Fernet (AES-128-CBC + HMAC, biblioteca
`cryptography`, já é dependência transitiva do projeto) — mecanismo diferente
do AES-256-GCM usado em portal_gestor/crypto.py (aquele é bit-compatível com o
dual-envelope do Gateway em Go; aqui não há nenhum par com o Gateway, então
Fernet é a escolha padrão/simples da biblioteca já presente, evitando
reinventar primitivas).

Chave: TISS_FERNET_KEY (settings/env), 32 bytes urlsafe-base64 gerados com
Fernet.generate_key(). Nunca comitar a chave real — só placeholder de dev em
.env.example.
"""
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class TISSCryptoError(Exception):
    """Erro ao cifrar/decifrar credencial de operadora."""


def _fernet() -> Fernet:
    key = getattr(settings, 'TISS_FERNET_KEY', None)
    if not key:
        raise TISSCryptoError('TISS_FERNET_KEY não configurada')
    if isinstance(key, str):
        key = key.encode()
    return Fernet(key)


def encrypt_credential(plain: str) -> str:
    """Cifra uma string em claro (login ou senha) e retorna token Fernet (str)."""
    if plain is None:
        return ''
    return _fernet().encrypt(plain.encode('utf-8')).decode('utf-8')


def decrypt_credential(token: str) -> str:
    """Decifra um token Fernet. Levanta TISSCryptoError se corrompido/chave errada."""
    if not token:
        return ''
    try:
        return _fernet().decrypt(token.encode('utf-8')).decode('utf-8')
    except InvalidToken as exc:
        raise TISSCryptoError('token de credencial inválido ou chave incorreta') from exc
