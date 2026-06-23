"""Unit tests for grocery finalisation math (re-pricing, pantry flags, totals).

Only the pure functions (``over_budget``, ``finalize``, ``format``) are tested —
``trim_to_budget`` / ``revise`` call the LLM and are covered by integration tests.
"""

from src.memory.schema import CurrentPlan, GroceryItem, Pantry, PantryItem, PriceEntry, Prices
from src.planner import grocery


def _plan(items, *, est_cost=None, budget=None, currency="INR", week_of="2026-06-22"):
    return CurrentPlan(
        week_of=week_of, currency=currency, budget=budget,
        est_cost=est_cost, grocery_list=list(items),
    )


def _prices(**items) -> Prices:
    entries = {k: PriceEntry(price=v["price"], unit=v.get("unit", "kg"),
                             per=v.get("per", v.get("unit", "kg")),
                             currency="INR")
               for k, v in items.items()}
    return Prices(entries=entries)


# ─── over_budget ──────────────────────────────────────────────────────────────


def test_over_budget_no_budget_returns_none():
    assert grocery.over_budget(_plan([], est_cost=100, budget=None)) is None


def test_over_budget_no_cost_returns_none():
    assert grocery.over_budget(_plan([], est_cost=None, budget=100)) is None


def test_over_budget_within_returns_zero():
    # within budget -> 0.0 (not None)
    assert grocery.over_budget(_plan([], est_cost=80, budget=100)) == 0.0


def test_over_budget_over_returns_excess():
    assert grocery.over_budget(_plan([], est_cost=150, budget=100)) == 50.0


# ─── finalize ─────────────────────────────────────────────────────────────────


def test_finalize_reprices_from_prices():
    plan = _plan([
        GroceryItem(item="rice", qty=2, unit="kg", est_price=200),  # LLM guess
    ])
    prices = _prices(rice={"price": 90})  # 90/kg -> 180 for 2kg
    out = grocery.finalize(plan, prices, Pantry())
    assert out.grocery_list[0].est_price == 180.0
    assert out.est_cost == 180.0


def test_finalize_flags_pantry_items():
    plan = _plan([
        GroceryItem(item="rice", qty=2, unit="kg", est_price=200),
        GroceryItem(item="onions", qty=1, unit="kg", est_price=40),
    ])
    prices = _prices(rice={"price": 90})
    pantry = Pantry(items={"rice": PantryItem(qty=10, unit="kg")})
    out = grocery.finalize(plan, prices, pantry)
    rice = next(g for g in out.grocery_list if g.item == "rice")
    onions = next(g for g in out.grocery_list if g.item == "onions")
    assert rice.in_pantry is True
    assert onions.in_pantry is False


def test_finalize_keeps_llm_estimate_when_no_price():
    plan = _plan([
        GroceryItem(item="milk", qty=1, unit="l", est_price=30),  # no price known
        GroceryItem(item="rice", qty=1, unit="kg", est_price=100),
    ])
    prices = _prices(rice={"price": 90})
    out = grocery.finalize(plan, prices, Pantry())
    milk = next(g for g in out.grocery_list if g.item == "milk")
    assert milk.est_price == 30  # kept
    assert milk.note and "no price" in milk.note
    assert out.est_cost == 120.0  # 90 (rice) + 30 (milk kept)


def test_finalize_unpriced_with_no_estimate_omitted_from_total():
    plan = _plan([
        GroceryItem(item="onions", qty=1, unit="kg", est_price=None),
        GroceryItem(item="rice", qty=1, unit="kg", est_price=None),
    ])
    prices = _prices(rice={"price": 90})
    out = grocery.finalize(plan, prices, Pantry())
    onions = next(g for g in out.grocery_list if g.item == "onions")
    assert onions.est_price is None
    assert onions.note and "no price" in onions.note
    assert out.est_cost == 90.0  # only rice priced


def test_finalize_no_prices_no_estimates_keeps_est_cost():
    plan = _plan([
        GroceryItem(item="onions", qty=1, unit="kg", est_price=None),
    ], est_cost=42.0)
    out = grocery.finalize(plan, Prices(), Pantry())
    assert out.est_cost == 42.0  # nothing priced -> unchanged


# ─── format ───────────────────────────────────────────────────────────────────


def test_format_empty_list():
    plan = _plan([])
    assert grocery.format(plan) == "🛒 Grocery list is empty."


def test_format_lists_items_and_total():
    plan = _plan([
        GroceryItem(item="rice", qty=2, unit="kg", est_price=180, currency="INR"),
        GroceryItem(item="milk", qty=1, unit="l", est_price=28, currency="INR"),
    ], est_cost=208, budget=300)
    out = grocery.format(plan)
    assert "rice" in out
    assert "milk" in out
    assert "208" in out
    assert "Within" in out and "300" in out


def test_format_pantry_tag():
    plan = _plan([
        GroceryItem(item="rice", qty=2, unit="kg", est_price=180,
                    currency="INR", in_pantry=True),
    ], est_cost=180)
    out = grocery.format(plan)
    assert "in pantry" in out


def test_format_over_budget_warning():
    plan = _plan([
        GroceryItem(item="rice", qty=2, unit="kg", est_price=180, currency="INR"),
    ], est_cost=180, budget=100)
    out = grocery.format(plan)
    assert "Over" in out
    assert "by" in out
    assert "80" in out  # 180 - 100
