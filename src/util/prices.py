"""Parse user-typed market prices into ``Prices`` memory.

Handles forms like:
    rice 90/kg
    rice ₹80 per kg
    basmati rice: 90/kg
    onions 40/kg, tomatoes 30/kg          (comma list)
    milk 28/litre
    eggs 6/dozen
    tomato 30 kg                          (trailing unit, no slash)
    chicken 220 per kg

Pure logic — unit-tested in ``tests/``.
"""

from __future__ import annotations

import re
from typing import Optional

from src.memory.schema import PriceEntry, Prices
from src.util import units
from src.util.dt import today_iso

# ─── currency detection ──────────────────────────────────────────────────────

_CURRENCY_SYMBOLS = {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP"}
_CURRENCY_WORDS = re.compile(r"\b(rs\.?|inr|usd|\$|eur|£|€|₹)\b", re.I)

# unit words we'll accept as a trailing bare unit (no slash / no "per")
_BARE_UNITS = (
    r"kg|kgs|kilograms?|g|gms?|grams?|ltr|l|litres?|liters?|ml|millilit(?:re|er)s?|"
    r"dozen|dz|pc|pcs|pieces?|each|bunch(?:es)?|packets?|packs?|box(?:es)?|"
    r"cups?|tbsp|tablespoons?|tsp|teaspoons?"
)

_PRICE_NUM = r"\d[\d.,]*\d|\d"  # 90, 90.5, 1,200

_RE_WITH_SEP = re.compile(
    rf"^(?P<item>.+?)\s*(?P<price>{_PRICE_NUM})\s*/\s*(?P<unit>[\w.-]+)\s*$", re.I
)
_RE_BARE_UNIT = re.compile(
    rf"^(?P<item>.+?)\s*(?P<price>{_PRICE_NUM})\s+(?P<unit>{_BARE_UNITS})\s*$", re.I
)
_RE_NO_UNIT = re.compile(
    rf"^(?P<item>.+?)\s*(?P<price>{_PRICE_NUM})\s*$", re.I
)


def _detect_currency(text: str) -> Optional[str]:
    for sym, code in _CURRENCY_SYMBOLS.items():
        if sym in text:
            return code
    m = _CURRENCY_WORDS.search(text)
    if m:
        w = m.group(1).lower().rstrip(".")
        return {"rs": "INR", "inr": "INR", "usd": "USD", "$": "USD",
                "eur": "EUR", "€": "EUR", "£": "GBP", "₹": "INR"}.get(w)
    return None


def _strip_currency(text: str) -> str:
    for sym in _CURRENCY_SYMBOLS:
        text = text.replace(sym, " ")
    text = _CURRENCY_WORDS.sub(" ", text)
    return text


def _clean_item(name: str) -> str:
    return name.strip().strip(":;,-=").strip().lower()


def _to_float(num: str) -> float:
    return float(num.replace(",", ""))


def parse_price_line(line: str, currency: Optional[str] = None) -> Optional[tuple[str, PriceEntry]]:
    """Parse a single price line into ``(item_key, PriceEntry)`` or None."""
    detected = _detect_currency(line)
    cur = currency or detected
    text = _strip_currency(line).strip().strip(".")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+per\s+", " / ", text, flags=re.I)  # "80 per kg" -> "80 / kg"
    if not text:
        return None

    for rx in (_RE_WITH_SEP, _RE_BARE_UNIT, _RE_NO_UNIT):
        m = rx.match(text)
        if not m:
            continue
        item = _clean_item(m.group("item"))
        price = _to_float(m.group("price"))
        unit_raw = m.groupdict().get("unit")
        unit = units.canonical_unit(unit_raw) if unit_raw else "pc"
        if not item or item.isdigit():
            continue
        entry = PriceEntry(
            price=price, currency=cur, unit=unit, per=unit,
            updated=today_iso(),
        )
        return item, entry
    return None


def _looks_like_entry(piece: str) -> bool:
    return bool(re.search(r"\d", piece))


def parse_price_block(text: str, currency: Optional[str] = None) -> list[tuple[str, PriceEntry]]:
    """Parse a multi-line / comma-separated block of prices."""
    results: list[tuple[str, PriceEntry]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # comma list only if every part has a digit (avoids "basmati rice, 90/kg" split)
        parts = [p.strip() for p in line.split(",")]
        if len(parts) > 1 and all(_looks_like_entry(p) for p in parts):
            pieces = parts
        else:
            pieces = [line]
        for piece in pieces:
            parsed = parse_price_line(piece, currency=currency)
            if parsed:
                results.append(parsed)
    return results


def merge_prices(prices: Prices, entries: list[tuple[str, PriceEntry]]) -> Prices:
    """Return a new ``Prices`` with ``entries`` merged in (item key -> lowercased)."""
    new_entries = {k: v.model_copy() for k, v in prices.entries.items()}
    for item, entry in entries:
        new_entries[item.lower()] = entry
    return Prices(entries=new_entries)


def format_prices(prices: Prices) -> str:
    if not prices.entries:
        return "(no prices known yet)"
    cur = next(iter(prices.entries.values())).currency or ""
    lines = []
    for item, e in sorted(prices.entries.items()):
        cur_sym = e.currency or cur or ""
        lines.append(f"• {item}: {cur_sym}{e.price:.0f}/{e.unit}" +
                     (f"  (updated {e.updated})" if e.updated else ""))
    return "\n".join(lines)
