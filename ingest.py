"""Recebimento do webhook: autentica, valida tamanho, grava no inbox (idempotente) e responde rápido.

Não consulta a API do provedor aqui: isso é trabalho do worker (retentável).
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Mapping

from .crypto import FieldCipher
from .domain import EVENT_QUARANTINED, EVENT_RECEIVED
from .errors import ParseError
from .providers.base import BankingProvider
from .repo.base import Repo

log = logging.getLogger("ingest")
MAX_WEBHOOK_BYTES = 256 * 1024


@dataclass(frozen=True)
class IngestResult:
    http_status: int
    outcome: str     # accepted | duplicate | invalid | rejected_auth | too_large


def ingest_webhook(*, provider: BankingProvider, repo: Repo, cipher: FieldCipher,
                   headers: Mapping[str, str], raw: bytes, now: Callable[[], datetime],
                   max_bytes: int = MAX_WEBHOOK_BYTES) -> IngestResult:
    ts = now()
    if len(raw) > max_bytes:
        log.warning("webhook rejeitado: corpo grande demais")
        return IngestResult(413, "too_large")
    if not provider.authenticate_webhook(headers):
        repo.append_audit(ts=ts, action="webhook_auth_failed", subject_type="provider",
                          subject_id=provider.name, details={"provider": provider.name})
        log.warning("webhook rejeitado: autenticação inválida")
        return IngestResult(401, "rejected_auth")

    aad = provider.name.encode()
    try:
        ev = provider.parse_webhook(raw)
    except ParseError:
        # Autenticado mas ilegível: guarda cifrado em quarentena e responde 200 (evita retry infinito).
        digest = hashlib.sha256(raw).hexdigest()
        res = repo.insert_event(provider=provider.name, entity="invalid", dedupe_key=f"invalid|{digest}",
                                body_hash=digest, payload_enc=cipher.encrypt(raw, aad),
                                status=EVENT_QUARANTINED, now=ts)
        if res.created:
            repo.append_audit(ts=ts, action="webhook_invalid_payload", subject_type="event",
                              subject_id=str(res.event_id), details={"reason": "parse_error"})
        return IngestResult(200, "invalid")

    res = repo.insert_event(provider=ev.provider, entity=ev.entity, dedupe_key=ev.dedupe_key,
                            body_hash=ev.body_hash, payload_enc=cipher.encrypt(raw, aad),
                            status=EVENT_RECEIVED, now=ts)
    if not res.created:
        if res.hash_conflict:
            repo.append_audit(ts=ts, action="webhook_conflict", subject_type="event",
                              subject_id=str(res.event_id), details={"reason": "same_key_different_body"})
            log.warning("evento repetido com corpo diferente event_id=%s", res.event_id)
        return IngestResult(200, "duplicate")
    log.info("evento recebido event_id=%s entity=%s", res.event_id, ev.entity)
    return IngestResult(200, "accepted")
