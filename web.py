"""API HTTP (FastAPI). Execute: uvicorn app.web:app --port 8000   (NÃO executado no ambiente do assistente)."""
from __future__ import annotations

from datetime import datetime, timezone

from .config import WebSettings
from .crypto import FieldCipher
from .ingest import MAX_WEBHOOK_BYTES, ingest_webhook
from .providers.celcoin import BasicWebhookAuth, CelcoinProvider
from .repo.postgres import PostgresRepo


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_app():
    from fastapi import FastAPI, Request, Response
    from fastapi.concurrency import run_in_threadpool

    settings = WebSettings.from_env()
    cipher = FieldCipher.from_env()
    provider = CelcoinProvider(None, BasicWebhookAuth(settings.webhook_login, settings.webhook_password))
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)   # sem docs públicas

    def _handle(headers: dict, raw: bytes) -> int:
        repo = PostgresRepo(settings.database_url)
        try:
            return ingest_webhook(provider=provider, repo=repo, cipher=cipher, headers=headers,
                                  raw=raw, now=_now).http_status
        finally:
            repo.close()

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/webhooks/celcoin")
    async def celcoin_webhook(request: Request):
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_WEBHOOK_BYTES:
            return Response(status_code=413)
        raw = await request.body()
        status = await run_in_threadpool(_handle, dict(request.headers), raw)
        return Response(status_code=status)

    return app


app = create_app() if __name__ != "__main__" and __import__("os").environ.get("DATABASE_URL") else None
