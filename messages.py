"""Textos para o dono (pt-BR). Curtos, sem o termo 'banco' e sem tom de instituição financeira."""
from __future__ import annotations

from typing import Optional

from .money import format_brl

TEMPLATES = {
    "pix_received": "Entrou um Pix de {valor} de {pagador}{ponto} Foi o quê?",
}

_CONNECTORS = {"de", "da", "do", "das", "dos", "e"}


def abbreviate_name(full: Optional[str]) -> str:
    """'MARIA DA SILVA SANTOS' -> 'Maria S.' (minimiza dado pessoal no canal de terceiros)."""
    if not full or not full.strip():
        return "um pagador"
    parts = [p for p in full.split() if p]
    first = parts[0].capitalize()
    rest = [p for p in parts[1:] if p.lower() not in _CONNECTORS]
    return f"{first} {rest[-1][0].upper()}." if rest else first


def render_pix_received(amount_cents: int, payer_name: Optional[str]) -> str:
    pagador = abbreviate_name(payer_name)
    ponto = "" if pagador.endswith(".") else "."        # evita "B.." quando o nome abreviado já termina em ponto
    return TEMPLATES["pix_received"].format(valor=format_brl(amount_cents), pagador=pagador, ponto=ponto)
