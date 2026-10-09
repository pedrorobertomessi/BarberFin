"""Trilha de auditoria append-only com encadeamento por hash.

Só chaves permitidas entram em `details`: nunca nome, documento, conta, telefone ou valor.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

GENESIS_HASH = "0" * 64
ALLOWED_DETAIL_KEYS = frozenset(
    {"reason", "entity", "status", "event_id", "attempts", "code", "provider", "outcome", "ledger_id", "message_id"}
)


@dataclass(frozen=True)
class AuditEntry:
    id: int
    ts: datetime
    action: str
    subject_type: str
    subject_id: str
    details: dict
    prev_hash: str
    hash: str


def sanitize_details(details: dict[str, Any]) -> dict[str, Any]:
    for key, value in details.items():
        if key not in ALLOWED_DETAIL_KEYS:
            raise ValueError(f"chave não permitida na auditoria: {key}")
        if not isinstance(value, (str, int, bool, type(None))):
            raise ValueError("valores de auditoria devem ser escalares")
    return dict(details)


def compute_hash(prev_hash: str, ts: datetime, action: str, subject_type: str, subject_id: str, details: dict) -> str:
    if ts.tzinfo is None:
        raise ValueError("ts deve ter fuso")
    body = json.dumps(
        [prev_hash, ts.astimezone(timezone.utc).isoformat(), action, subject_type, subject_id, details],
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def verify_chain(entries: Iterable[AuditEntry]) -> bool:
    prev = GENESIS_HASH
    for e in entries:
        if e.prev_hash != prev:
            return False
        if compute_hash(e.prev_hash, e.ts, e.action, e.subject_type, e.subject_id, e.details) != e.hash:
            return False
        prev = e.hash
    return True
