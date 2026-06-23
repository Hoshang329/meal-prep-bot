"""Unit normalisation, servings scaling, grocery aggregation, and cost math.

Pure logic — no Telegram, no LLM. Unit-tested in ``tests/``.

Conversions are only attempted within the same physical dimension:
- mass:   g, kg
- volume: ml, l (cup/tbsp/tsp are treated as volume and converted to ml)
Everything else (pc, dozen, bunch, packet, box, …) is kept as-is; cross-dimension
conversion (e.g. cups of rice -> kg) would need density and is deliberately NOT
faked. When cost can't be computed, ``estimate_cost`` returns ``None``.
"""

from __future__ import annotations

from typing import Optional

from src.memory.schema import GroceryItem, Ingredient, Pantry, PriceEntry, Prices

# ─── Unit canonicalisation ───────────────────────────────────────────────────

_UNIT_ALIASES: dict[str, str] = {
    "kg": "kg", "kgs": "kg", "kilogram": "kg", "kilograms": "kg",
    "g": "g", "gm": "g", "gms": "g", "gram": "g", "grams": "g",
    "l": "l", "ltr": "l", "litre": "l", "liter": "l", "litres": "l", "liters": "l",
    "ml": "ml", "millilitre": "ml", "milliliter": "ml", "cc": "ml",
    "cup": "cup", "cups": "cup",
    "tbsp": "tbsp", "tablespoon": "tbsp", "tablespoons": "tbsp",
    "tsp": "tsp", "teaspoon": "tsp", "teaspoons": "tsp",
    "pc": "pc", "piece": "pc", "pieces": "pc", "pcs": "pc", "each": "pc",
    "dozen": "dozen", "dz": "dozen",
    "bunch": "bunch", "bunches": "bunch",
    "packet": "packet", "pack": "packet", "packs": "packet", "packets": "packet",
    "box": "box", "boxes": "box",
    "unit": "pc",
}

# Conversion factor to the dimension's base unit (mass->g, volume->ml).
_TO_BASE: dict[str, float] = {
    # mass -> grams
    "g": 1.0, "kg": 1000.0,
    # volume -> millilitres
    "ml": 1.0, "l": 1000.0, "cup": 240.0, "tbsp": 15.0, "tsp": 5.0,
}

_MASS = {"g", "kg"}
_VOLUME = {"ml", "l", "cup", "tbsp", "tsp"}


def canonical_unit(u: str | None) -> str:
    if not u:
        return "pc"
    key = u.strip().lower().rstrip(".")
    return _UNIT_ALIASES.get(key, key or "pc")


def _dim(u: str) -> str | None:
    c = canonical_unit(u)
    if c in _MASS:
        return "mass"
    if c in _VOLUME:
        return "volume"
    return None


def convert(qty: float, from_u: str, to_u: str) -> Optional[float]:
    """Convert ``qty`` from ``from_u`` to ``to_u`` if same dimension; else None."""
    cf, ct = canonical_unit(from_u), canonical_unit(to_u)
    if cf == ct:
        return float(qty)
    df, dt = _dim(cf), _dim(ct)
    if df is None or df != dt:
        return None  # different dimensions (or non-metric) -> can't convert
    # both in same dim -> convert via base unit
    base_f = _TO_BASE[cf]
    base_t = _TO_BASE[ct]
    return float(qty) * base_f / base_t


# ─── Scaling & aggregation ───────────────────────────────────────────────────


def scale_ingredient(ing: Ingredient, factor: float) -> Ingredient:
    if ing.qty is None or factor == 1.0:
        return ing.model_copy()
    return ing.model_copy(update={"qty": round(ing.qty * factor, 3)})


def scale_ingredients(items: list[Ingredient], base_servings: float, target_servings: float) -> list[Ingredient]:
    if not base_servings:
        return [i.model_copy() for i in items]
    factor = target_servings / base_servings
    return [scale_ingredient(i, factor) for i in items]


def aggregate_ingredients(items: list[Ingredient]) -> dict[tuple[str, str], float]:
    """Sum quantities by (item name, canonical unit). Items with no qty are skipped."""
    agg: dict[tuple[str, str], float] = {}
    for ing in items:
        if ing.qty is None:
            continue
        item = ing.item.strip().lower()
        cu = canonical_unit(ing.unit)
        agg[(item, cu)] = agg.get((item, cu), 0.0) + ing.qty
    return agg


# ─── Pantry subtraction ──────────────────────────────────────────────────────


def apply_pantry(agg: dict[tuple[str, str], float], pantry: Pantry) -> dict[tuple[str, str], float]:
    """Subtract on-hand stock (same dimension) from the aggregate. Non-negative."""
    result = dict(agg)
    for item, pi in pantry.items.items():
        key = (item.strip().lower(), canonical_unit(pi.unit))
        if key not in result:
            # try to find same item with a convertible unit
            for (it, cu), qty in list(result.items()):
                if it == key[0]:
                    conv = convert(pi.qty, pi.unit, cu)
                    if conv is not None:
                        result[(it, cu)] = max(0.0, qty - conv)
                    break
            continue
        result[key] = max(0.0, result[key] - pi.qty)
    return {k: v for k, v in result.items() if v > 1e-9}


# ─── Cost estimation ─────────────────────────────────────────────────────────


def _match_price_entry(prices: Prices, item: str) -> PriceEntry | None:
    """Find a price entry for ``item`` (exact, then lowercased, then contains)."""
    key = item.strip().lower()
    entries = prices.entries
    # exact
    for name, e in entries.items():
        if name.strip().lower() == key:
            return e
    # contains (item name within stored name or vice versa)
    for name, e in entries.items():
        nk = name.strip().lower()
        if key in nk or nk in key:
            return e
    return None


def estimate_cost(item: str, qty: float, unit: str, prices: Prices, currency: str | None = None) -> tuple[Optional[float], Optional[str]]:
    """Estimate cost of ``qty`` ``unit`` of ``item`` from ``prices``.

    Returns ``(cost, note)``. ``cost`` is None when no price is known or the units
    can't be reconciled (different dimensions). ``note`` explains why when None.
    """
    entry = _match_price_entry(prices, item)
    if entry is None:
        return None, "no price known"
    per_unit = entry.per or entry.unit
    conv = convert(qty, unit, per_unit)
    if conv is None:
        return None, f"units '{unit}' vs price-per '{per_unit}' not convertible"
    cost = entry.price * conv
    return round(cost, 2), None


def to_grocery_items(
    agg: dict[tuple[str, str], float],
    prices: Prices,
    pantry: Pantry | None = None,
    currency: str | None = None,
) -> list[GroceryItem]:
    """Turn an aggregated {(item, unit): qty} map into priced GroceryItems."""
    pantry = pantry or Pantry()
    after_pantry = apply_pantry(agg, pantry)
    items: list[GroceryItem] = []
    for (item, unit), qty in sorted(after_pantry.items()):
        # was this fully covered by pantry?
        in_pantry = qty < 1e-9
        if in_pantry:
            continue
        cost, note = estimate_cost(item, qty, unit, prices, currency)
        items.append(GroceryItem(
            item=item, qty=round(qty, 3), unit=unit,
            est_price=cost, currency=currency or (prices.entries.get(item) and prices.entries[item].currency),
            in_pantry=False, note=note,
        ))
    return items


def total_cost(items: list[GroceryItem]) -> Optional[float]:
    """Sum est_price across items that have one; None if none priced."""
    priced = [i.est_price for i in items if i.est_price is not None]
    if not priced:
        return None
    return round(sum(priced), 2)
