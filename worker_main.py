"""Loop do worker. Execute: python -m app.worker_main   (NÃO executado no ambiente do assistente)."""
from __future__ import annotations

import logging
import signal
import time
from datetime import datetime, timezone

from .channels import ConsoleChannel
from .config import WorkerSettings
from .crypto import FieldCipher
from .providers.celcoin import BasicWebhookAuth, CelcoinClient, CelcoinProvider
from .providers.http_httpx import HttpxHttp
from .repo.postgres import PostgresRepo
from .worker import Worker


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = WorkerSettings.from_env()
    if s.env == "production":
        raise SystemExit("ConsoleChannel não pode ser usado em produção; o canal WhatsApp entra na etapa (b).")
    client = CelcoinClient(HttpxHttp(s.mtls_cert, s.mtls_key), s.celcoin_base_url, s.client_id, s.client_secret)
    provider = CelcoinProvider(client, BasicWebhookAuth(s.webhook_login, s.webhook_password))
    worker = Worker(PostgresRepo(s.database_url), provider, FieldCipher.from_env())
    channel, stop = ConsoleChannel(), False

    def _stop(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    while not stop:
        now = datetime.now(timezone.utc)
        busy = worker.run_once(now)
        busy = worker.dispatch_outbox(now, channel) > 0 or busy
        if not busy:
            time.sleep(1.0)


if __name__ == "__main__":
    main()
