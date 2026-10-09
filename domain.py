from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# Estados do evento no inbox
EVENT_RECEIVED = "received"
EVENT_RETRY = "retry"
EVENT_PROCESSING = "processing"
EVENT_PROCESSED = "processed"
EVENT_IGNORED = "ignored"
EVENT_QUARANTINED = "quarantined"


@dataclass(frozen=True)
class PixReceipt:
    """Pix recebido, já normalizado (valores em centavos)."""
    end_to_end_id: str
    provider_tx_id: Optional[str]
    amount_cents: int
    status: str                      # CONFIRMED | PENDING | ...
    credit_account: Optional[str]
    payer_name: Optional[str] = None
    payer_tax_id: Optional[str] = None
    initiation_type: Optional[str] = None
    transaction_identification: Optional[str] = None
    provider_ts_raw: Optional[str] = None


@dataclass(frozen=True)
class ProviderEvent:
    provider: str
    entity: str
    status: str
    dedupe_key: str
    body_hash: str
    receipt: Optional[PixReceipt] = None


@dataclass
class StoredEvent:
    id: int
    provider: str
    entity: str
    dedupe_key: str
    body_hash: str
    payload_enc: bytes
    status: str
    attempts: int
    next_attempt_at: datetime
    received_at: datetime
    lease_until: Optional[datetime] = None
    last_error: Optional[str] = None


@dataclass
class Customer:
    id: int
    provider_account: str
    status: str = "active"
    phone_enc: Optional[bytes] = None


@dataclass(frozen=True)
class NewLedgerEntry:
    customer_id: int
    direction: str                   # 'in' | 'out'
    amount_cents: int
    end_to_end_id: str
    provider_tx_id: Optional[str]
    counterparty_name_enc: Optional[bytes]
    counterparty_doc_idx: Optional[str]
    initiation_type: Optional[str]
    transaction_identification: Optional[str]
    occurred_at: datetime
    source_event_id: int


@dataclass(frozen=True)
class LedgerEntry:
    id: int
    customer_id: int
    direction: str
    amount_cents: int
    end_to_end_id: str
    provider_tx_id: Optional[str]
    counterparty_name_enc: Optional[bytes]
    counterparty_doc_idx: Optional[str]
    initiation_type: Optional[str]
    transaction_identification: Optional[str]
    occurred_at: datetime
    source_event_id: int


@dataclass
class OutboxMessage:
    id: int
    customer_id: int
    kind: str
    payload: dict
    dedupe_key: str
    status: str                      # pending | sending | sent | failed
    attempts: int
    next_attempt_at: datetime
    created_at: datetime
    lease_until: Optional[datetime] = None
    last_error: Optional[str] = None


@dataclass(frozen=True)
class InsertResult:
    event_id: int
    created: bool
    hash_conflict: bool = False
