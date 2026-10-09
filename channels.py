"""Porta de mensageria. O adaptador do WhatsApp Cloud API entra na etapa (b)."""
from __future__ import annotations

from typing import Protocol


class MessagingChannel(Protocol):
    def send_text(self, *, customer_id: int, text: str) -> None:
        """Levanta exceção se o envio falhar."""


class ConsoleChannel:
    """Desenvolvimento local: imprime no terminal (nunca usar em produção: imprime valores)."""

    def send_text(self, *, customer_id: int, text: str) -> None:
        print(f"[para cliente #{customer_id}] {text}")


class RecordingChannel:
    """Testes."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.fail_next = 0

    def send_text(self, *, customer_id: int, text: str) -> None:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("falha simulada")
        self.sent.append((customer_id, text))
