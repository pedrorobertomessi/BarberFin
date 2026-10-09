"""Cifra de campos (AES-256-GCM) e índice cego (HMAC-SHA256).

MVP: chaves vêm do ambiente. Evolução: envelope encryption com KMS (a chave de dados
é cifrada por uma chave mestra do KMS). O byte de versão permite rotação.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = b"\x01"


class CryptoError(Exception):
    pass


class FieldCipher:
    def __init__(self, enc_key: bytes, index_key: bytes) -> None:
        if len(enc_key) != 32 or len(index_key) != 32:
            raise CryptoError("as chaves devem ter 32 bytes")
        if enc_key == index_key:
            raise CryptoError("use chaves distintas para cifra e índice")
        self._aead = AESGCM(enc_key)
        self._index_key = index_key

    @classmethod
    def from_env(cls) -> "FieldCipher":
        try:
            enc = base64.b64decode(os.environ["FIELD_ENC_KEY"], validate=True)
            idx = base64.b64decode(os.environ["FIELD_INDEX_KEY"], validate=True)
        except (KeyError, ValueError) as exc:
            raise CryptoError("FIELD_ENC_KEY/FIELD_INDEX_KEY ausentes ou inválidas") from exc
        return cls(enc, idx)

    def encrypt(self, plaintext: bytes, aad: bytes = b"") -> bytes:
        nonce = os.urandom(12)
        return _VERSION + nonce + self._aead.encrypt(nonce, plaintext, aad)

    def decrypt(self, blob: bytes, aad: bytes = b"") -> bytes:
        if len(blob) < 1 + 12 + 16 or blob[:1] != _VERSION:
            raise CryptoError("blob inválido")
        try:
            return self._aead.decrypt(blob[1:13], blob[13:], aad)
        except Exception as exc:  # InvalidTag e afins
            raise CryptoError("falha ao decifrar") from exc

    def blind_index(self, value: str) -> str:
        """Índice determinístico para buscas por igualdade (telefone, CPF/CNPJ)."""
        norm = re.sub(r"\D", "", value) or value.strip().lower()
        return hmac.new(self._index_key, norm.encode(), hashlib.sha256).hexdigest()
