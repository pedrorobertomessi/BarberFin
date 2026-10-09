"""Dinheiro: sempre centavos inteiros. Nunca float."""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import MoneyError

MAX_CENTS = 10**12  # teto de sanidade: R$ 10 bilhões


def to_cents(value: Any) -> int:
    """Converte um valor em REAIS (Decimal, int ou str) para centavos inteiros.

    Rejeita float/bool, valores negativos, não finitos e mais de 2 casas decimais
    (nunca arredonda silenciosamente).
    """
    if isinstance(value, (bool, float)):
        raise MoneyError("float/bool não é aceito; use Decimal")
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, str):
        try:
            d = Decimal(value.strip())
        except InvalidOperation as exc:
            raise MoneyError("valor inválido") from exc
    else:
        raise MoneyError("tipo de valor não suportado")
    if not d.is_finite():
        raise MoneyError("valor não finito")
    cents = d * 100
    if cents != cents.to_integral_value():
        raise MoneyError("mais de duas casas decimais")
    n = int(cents)
    if n < 0:
        raise MoneyError("valor negativo")
    if n > MAX_CENTS:
        raise MoneyError("valor acima do teto")
    return n


def loads_decimal(raw: bytes | str) -> Any:
    """json.loads com números decimais como Decimal (nunca float)."""
    return json.loads(raw, parse_float=Decimal)


def format_brl(cents: int) -> str:
    """12345 -> 'R$ 123,45'; 123456 -> 'R$ 1.234,56'."""
    if not isinstance(cents, int) or isinstance(cents, bool) or cents < 0:
        raise MoneyError("centavos inválidos")
    reais, resto = divmod(cents, 100)
    return "R$ " + f"{reais:,}".replace(",", ".") + f",{resto:02d}"
