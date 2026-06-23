"""Unit tests for unit normalisation, conversion, aggregation, and cost math."""

import pytest

from src.memory.schema import Ingredient, Pantry, PantryItem, PriceEntry, Prices
from src.util import units as U


# ─── canonical_unit ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("kg", "kg"), ("kgs", "kg"), ("kilograms", "kg"),
    ("g", "g"), ("gms", "g"), ("grams", "g"),
    ("l", "l"), ("litre", "l"), ("liters", "l"), ("ltr", "l"),
    ("ml", "ml"), ("cc", "ml"),
    ("cup", "cup"), ("cups", "cup"),
    ("tbsp", "tbsp"), ("tablespoon", "tbsp"),
    ("tsp", "tsp"),
    ("pc", "pc"), ("piece", "pc"), ("pcs", "pc"), ("each", "pc"),
    ("dozen", "dozen"), ("dz", "dozen"),
    ("bunch", "bunch"), ("bunches", "bunch"),
    ("packet", "packet"), ("pack", "packet"),
    ("KG", "kg"), ("Kg.", "kg"),
])
def test_canonical_unit(raw, expected):
    assert U.canonical_unit(raw) == expected


def test_canonical_unit_empty_defaults_pc():
    assert U.canonical_unit("") == "pc"
    assert U.canonical_unit(None) == "pc"
    assert U.canonical_unit("   ") == "pc"


def test_canonical_unit_unknown_passthrough():
    # unknown unit strings are returned lowercased as-is
    assert U.canonical_unit("loaf") == "loaf"


# ─── convert ──────────────────────────────────────────────────────────────────


def test_convert_kg_to_g():
    assert U.convert(2, "kg", "g") == 2000.0


def test_convert_g_to_kg():
    assert U.convert(500, "g", "kg") == 0.5


def test_convert_l_to_ml():
    assert U.convert(1, "l", "ml") == 1000.0


def test_convert_cup_to_ml():
    assert U.convert(1, "cup", "ml") == 240.0


def test_convert_same_unit():
    assert U.convert(3, "kg", "kg") == 3.0


def test_convert_different_dimensions_returns_none():
    assert U.convert(1, "kg", "l") is None
    assert U.convert(1, "pc", "kg") is None


def test_convert_non_metric_returns_none():
    # dozen and pc have no base conversion factor
    assert U.convert(1, "dozen", "pc") is None
    assert U.convert(2, "bunch", "kg") is None


# ─── scaling ──────────────────────────────────────────────────────────────────


def test_scale_ingredient_doubles():
    ing = Ingredient(item="rice", qty=2, unit="kg")
    out = U.scale_ingredient(ing, 2.0)
    assert out.qty == 4.0
    assert out.item == "rice"
    # original untouched
    assert ing.qty == 2


def test_scale_ingredients_factor():
    items = [Ingredient(item="rice", qty=2, unit="kg"),
             Ingredient(item="dal", qty=100, unit="g")]
    out = U.scale_ingredients(items, base_servings=2, target_servings=4)
    assert out[0].qty == 4.0
    assert out[1].qty == 200.0


def test_scale_ingredients_zero_base_no_scale():
    items = [Ingredient(item="rice", qty=2, unit="kg")]
    out = U.scale_ingredients(items, base_servings=0, target_servings=4)
    assert out[0].qty == 2  # unchanged


# ─── aggregation ──────────────────────────────────────────────────────────────


def test_aggregate_sums_same_unit():
    items = [
        Ingredient(item="rice", qty=2, unit="kg"),
        Ingredient(item="rice", qty=1, unit="kg"),
        Ingredient(item="rice", qty=500, unit="g"),
    ]
    agg = U.aggregate_ingredients(items)
    # "kg" and "g" are different canonical units -> separate keys (no cross-unit sum)
    assert agg[("rice", "kg")] == 3.0
    assert agg[("rice", "g")] == 500.0


def test_aggregate_normalises_unit_aliases():
    items = [
        Ingredient(item="milk", qty=1, unit="litre"),
        Ingredient(item="milk", qty=0.5, unit="l"),
    ]
    agg = U.aggregate_ingredients(items)
    assert agg[("milk", "l")] == 1.5


def test_aggregate_skips_missing_qty():
    items = [Ingredient(item="salt"), Ingredient(item="rice", qty=1, unit="kg")]
    agg = U.aggregate_ingredients(items)
    assert ("salt", "pc") not in agg
    assert agg[("rice", "kg")] == 1.0


# ─── pantry subtraction ───────────────────────────────────────────────────────


def test_apply_pantry_same_unit():
    agg = {("rice", "kg"): 2.0}
    pantry = Pantry(items={"rice": PantryItem(qty=0.5, unit="kg")})
    out = U.apply_pantry(agg, pantry)
    assert out[("rice", "kg")] == 1.5


def test_apply_pantry_convertible_unit():
    agg = {("rice", "kg"): 2.0}
    pantry = Pantry(items={"rice": PantryItem(qty=500, unit="g")})
    out = U.apply_pantry(agg, pantry)
    assert out[("rice", "kg")] == 1.5  # 2kg - 0.5kg


def test_apply_pantry_fully_covered_removed():
    agg = {("rice", "kg"): 1.0}
    pantry = Pantry(items={"rice": PantryItem(qty=2, unit="kg")})
    out = U.apply_pantry(agg, pantry)
    assert ("rice", "kg") not in out  # fully covered -> dropped


def test_apply_pantry_non_negative():
    agg = {("rice", "kg"): 1.0}
    pantry = Pantry(items={"rice": PantryItem(qty=5, unit="kg")})
    out = U.apply_pantry(agg, pantry)
    # not present (clamped to 0 then dropped)
    assert ("rice", "kg") not in out


# ─── cost estimation ──────────────────────────────────────────────────────────


def _prices(**items) -> Prices:
    entries = {k: PriceEntry(price=v["price"], unit=v.get("unit", "kg"),
                             per=v.get("per", v.get("unit", "kg")),
                             currency=v.get("currency", "INR"))
               for k, v in items.items()}
    return Prices(entries=entries)


def test_estimate_cost_basic():
    prices = _prices(rice={"price": 90})
    cost, note = U.estimate_cost("rice", 2, "kg", prices)
    assert cost == 180.0
    assert note is None


def test_estimate_cost_unit_conversion():
    prices = _prices(rice={"price": 90})  # per kg
    cost, note = U.estimate_cost("rice", 500, "g", prices)
    assert cost == 45.0  # 0.5 kg * 90


def test_estimate_cost_no_price():
    prices = Prices()
    cost, note = U.estimate_cost("rice", 2, "kg", prices)
    assert cost is None
    assert "no price" in note


def test_estimate_cost_unconvertible_units():
    prices = _prices(rice={"price": 90})  # per kg
    cost, note = U.estimate_cost("rice", 3, "pc", prices)
    assert cost is None
    assert "not convertible" in note


def test_estimate_cost_contains_match():
    # "basmati rice" should match a stored "rice" entry via contains rule
    prices = _prices(rice={"price": 90})
    cost, note = U.estimate_cost("basmati rice", 1, "kg", prices)
    assert cost == 90.0


# ─── to_grocery_items + total_cost ────────────────────────────────────────────


def test_to_grocery_items_priced_and_pantry():
    agg = {("rice", "kg"): 2.0, ("onions", "kg"): 1.0}
    prices = _prices(rice={"price": 90})
    pantry = Pantry(items={"onions": PantryItem(qty=1, unit="kg")})
    items = U.to_grocery_items(agg, prices, pantry=pantry, currency="INR")
    by_item = {i.item: i for i in items}
    assert "onions" not in by_item  # fully covered by pantry -> dropped
    assert by_item["rice"].est_price == 180.0
    assert by_item["rice"].currency == "INR"


def test_total_cost_sums_priced_only():
    from src.memory.schema import GroceryItem
    items = [
        GroceryItem(item="rice", qty=2, unit="kg", est_price=180),
        GroceryItem(item="onions", qty=1, unit="kg", est_price=None),
        GroceryItem(item="milk", qty=1, unit="l", est_price=28),
    ]
    assert U.total_cost(items) == 208.0


def test_total_cost_none_when_nothing_priced():
    from src.memory.schema import GroceryItem
    items = [GroceryItem(item="rice", qty=2, unit="kg", est_price=None)]
    assert U.total_cost(items) is None
