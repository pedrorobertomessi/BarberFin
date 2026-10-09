"""Configuração por variáveis de ambiente. Falha cedo se algo obrigatório faltar.

O processo web NÃO recebe credenciais da API do provedor (privilégio mínimo).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _req(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise RuntimeError(f"variável de ambiente obrigatória ausente: {name}")
    return v


@dataclass(frozen=True)
class WebSettings:
    database_url: str
    webhook_login: str
    webhook_password: str

    @classmethod
    def from_env(cls) -> "WebSettings":
        return cls(_req("DATABASE_URL"), _req("CELCOIN_WEBHOOK_LOGIN"), _req("CELCOIN_WEBHOOK_PASSWORD"))


@dataclass(frozen=True)
class WorkerSettings(WebSettings):
    celcoin_base_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    mtls_cert: str = ""
    mtls_key: str = ""
    env: str = "development"

    @classmethod
    def from_env(cls) -> "WorkerSettings":
        return cls(_req("DATABASE_URL"), _req("CELCOIN_WEBHOOK_LOGIN"), _req("CELCOIN_WEBHOOK_PASSWORD"),
                   _req("CELCOIN_BASE_URL"), _req("CELCOIN_CLIENT_ID"), _req("CELCOIN_CLIENT_SECRET"),
                   _req("CELCOIN_MTLS_CERT"), _req("CELCOIN_MTLS_KEY"), os.environ.get("APP_ENV", "development"))
