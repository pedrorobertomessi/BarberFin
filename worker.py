"""Worker: processa eventos do inbox e despacha o outbox. Idempotente e reprocessável.

Regra de ouro: o webhook é só um gatilho. Antes de registrar um Pix e avisar o dono,
o fato é confirmado por consulta autoritativa ao provedor.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from .channels import MessagingChannel
from .crypto import CryptoError, FieldCipher
from .domain import NewLedgerEntry, PixReceipt, StoredEvent
from .errors import ParseError, ProviderError
from .messages import render_pix_received
from .providers.base import BankingProvider
from .repo.base import Repo

log = logging.getLogger("worker")

MAX_EVENT_ATTEMPTS = 8
MAX_MESSAGE_ATTEMPTS = 5
LEASE_SECONDS = 60


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(5 * 2 ** max(attempts, 0), 900))


class Worker:
    def __init__(self, repo: Repo, provider: BankingProvider, cipher: FieldCipher) -> None:
        self.repo, self.provider, self.cipher = repo, provider, cipher

    # ------------------------------------------------------------ eventos
    def run_once(self, now: datetime) -> bool:
        ev = self.repo.claim_next_event(now=now, lease_seconds=LEASE_SECONDS)
        if ev is None:
            return False
        self._process(ev, now)
        return True

    def _audit(self, now: datetime, action: str, ev: StoredEvent, **details) -> None:
        self.repo.append_audit(ts=now, action=action, subject_type="event", subject_id=str(ev.id), details=details)

    def _quarantine(self, ev: StoredEvent, now: datetime, reason: str) -> None:
        with self.repo.transaction():
            self.repo.mark_quarantined(ev.id, reason, now)
            self._audit(now, "event_quarantined", ev, reason=reason)
        log.error("evento em quarentena event_id=%s reason=%s", ev.id, reason)

    def _retry_or_quarantine(self, ev: StoredEvent, now: datetime, code: str) -> None:
        if ev.attempts >= MAX_EVENT_ATTEMPTS:
            self._quarantine(ev, now, f"max_attempts:{code}")
            return
        self.repo.mark_retry(ev.id, now + backoff(ev.attempts), code, now)
        log.warning("evento será reprocessado event_id=%s code=%s attempts=%s", ev.id, code, ev.attempts)

    def _process(self, ev: StoredEvent, now: datetime) -> None:
        try:
            raw = self.cipher.decrypt(ev.payload_enc, aad=ev.provider.encode())
            pe = self.provider.parse_webhook(raw)
        except (CryptoError, ParseError):
            self._quarantine(ev, now, "unreadable_payload")
            return

        if pe.entity != "pix-payment-in" or pe.receipt is None:
            self.repo.mark_ignored(ev.id, "entity_not_handled", now)   # saídas/devoluções: etapas futuras
            return
        hook = pe.receipt
        if pe.status != "CONFIRMED":
            self.repo.mark_ignored(ev.id, "status_not_confirmed", now)
            return

        customer = self.repo.find_customer_by_account(hook.credit_account or "")
        if customer is None:
            self._quarantine(ev, now, "unknown_account")
            return

        # Confirmação autoritativa (nunca confiar só no webhook).
        try:
            real = self.provider.fetch_pix_receipt(end_to_end_id=hook.end_to_end_id)
        except ProviderError as exc:
            self._retry_or_quarantine(ev, now, type(exc).__name__)
            return

        if real.status == "PENDING":                       # ex.: bloqueio cautelar no provedor
            self._retry_or_quarantine(ev, now, "pending_at_provider")
            return
        if real.status != "CONFIRMED":
            self._quarantine(ev, now, "provider_status_not_confirmed")
            return
        if real.end_to_end_id != hook.end_to_end_id:
            self._quarantine(ev, now, "e2e_mismatch")
            return
        if real.amount_cents != hook.amount_cents:
            self._quarantine(ev, now, "amount_mismatch")
            return
        if real.credit_account and real.credit_account != hook.credit_account:
            self._quarantine(ev, now, "account_mismatch")
            return

        self._register_pix(ev, customer.id, real, hook, now)

    def _register_pix(self, ev: StoredEvent, customer_id: int, real: PixReceipt, hook: PixReceipt,
                      now: datetime) -> None:
        payer_name = real.payer_name or hook.payer_name
        payer_doc = real.payer_tax_id or hook.payer_tax_id
        entry = NewLedgerEntry(
            customer_id=customer_id, direction="in", amount_cents=real.amount_cents,
            end_to_end_id=real.end_to_end_id, provider_tx_id=real.provider_tx_id or hook.provider_tx_id,
            counterparty_name_enc=self.cipher.encrypt(payer_name.encode(), b"ledger:name") if payer_name else None,
            counterparty_doc_idx=self.cipher.blind_index(payer_doc) if payer_doc else None,
            initiation_type=real.initiation_type or hook.initiation_type,
            transaction_identification=real.transaction_identification or hook.transaction_identification,
            occurred_at=now,        # dia comercial usa o NOSSO relógio (o fuso do provedor é A CONFERIR)
            source_event_id=ev.id)
        with self.repo.transaction():                      # ledger + outbox + estado: tudo ou nada
            ledger_id, created = self.repo.insert_ledger_entry(entry)
            if created:
                self.repo.enqueue_message(customer_id=customer_id, kind="pix_received",
                                          payload={"ledger_id": ledger_id}, dedupe_key=f"pix_in:{real.end_to_end_id}",
                                          now=now)
            self.repo.mark_processed(ev.id, now)
            self.repo.append_audit(ts=now, action="pix_in_registered" if created else "pix_in_duplicate_ignored",
                                   subject_type="ledger", subject_id=str(ledger_id),
                                   details={"event_id": ev.id, "outcome": "created" if created else "duplicate"})
        log.info("pix registrado ledger_id=%s created=%s", ledger_id, created)

    # ------------------------------------------------------------ outbox
    def dispatch_outbox(self, now: datetime, channel: MessagingChannel, limit: int = 20) -> int:
        sent = 0
        for msg in self.repo.claim_pending_messages(now=now, limit=limit, lease_seconds=LEASE_SECONDS):
            try:
                if msg.kind != "pix_received":
                    raise ValueError("kind_desconhecido")
                entry = self.repo.get_ledger_entry(msg.payload["ledger_id"])
                name = (self.cipher.decrypt(entry.counterparty_name_enc, b"ledger:name").decode()
                        if entry.counterparty_name_enc else None)
                channel.send_text(customer_id=msg.customer_id, text=render_pix_received(entry.amount_cents, name))
            except Exception as exc:                       # noqa: BLE001 - qualquer falha vira retry
                give_up = msg.attempts >= MAX_MESSAGE_ATTEMPTS
                self.repo.mark_message_retry(msg.id, now + backoff(msg.attempts), type(exc).__name__, give_up)
                if give_up:
                    self.repo.append_audit(ts=now, action="message_failed", subject_type="message",
                                           subject_id=str(msg.id), details={"code": type(exc).__name__})
                log.warning("falha ao enviar message_id=%s code=%s", msg.id, type(exc).__name__)
                continue
            self.repo.mark_message_sent(msg.id, now)
            sent += 1
        return sent
