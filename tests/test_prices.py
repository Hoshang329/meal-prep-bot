"""Unit tests for price parsing (pure logic, no network/Telegram)."""

from src.memory.schema import PriceEntry, Prices
from src.util import prices as P


# ─── single-line parsing ─────────────────────────────────────────────────────


def test_parse_simple_slash():
    item, e = P.parse_price_line("rice 90/kg", currency="INR")
    assert item == "rice"
    assert e.price == 90.0
    assert e.unit == "kg"
    assert e.per == "kg"
    assert e.currency == "INR"


def test_parse_currency_symbol_detected():
    item, e = P.parse_price_line("rice ₹80 per kg")
    assert item == "rice"
    assert e.price == 80.0
    assert e.unit == "kg"
    assert e.currency == "INR"  # detected from the ₹ symbol


def test_parse_dozen():
    item, e = P.parse_price_line("eggs 6/dozen")
    assert item == "eggs"
    assert e.price == 6.0
    assert e.unit == "dozen"


def test_parse_litire_alias_canonicalised():
    item, e = P.parse_price_line("milk 28/litre")
    assert item == "milk"
    assert e.unit == "l"  # "litre" -> canonical "l"


def test_parse_bare_unit_no_slash():
    item, e = P.parse_price_line("tomato 30 kg")
    assert item == "tomato"
    assert e.price == 30.0
    assert e.unit == "kg"


def test_parse_strips_label_colon():
    item, e = P.parse_price_line("basmati rice: 90/kg")
    assert item == "basmati rice"
    assert e.price == 90.0


def test_parse_per_word():
    item, e = P.parse_price_line("chicken 220 per kg")
    assert item == "chicken"
    assert e.price == 220.0
    assert e.unit == "kg"


def test_parse_comma_thousands():
    item, e = P.parse_price_line("rice 1,200/kg")
    assert e.price == 1200.0


def test_parse_garbage_returns_none():
    assert P.parse_price_line("just rice") is None
    assert P.parse_price_line("") is None
    assert P.parse_price_line("   ") is None


def test_parse_digit_only_item_rejected():
    # "123 90/kg" -> item would be "123" which isdigit() -> rejected
    assert P.parse_price_line("123 90/kg") is None


# ─── block parsing (multi-line / comma list) ─────────────────────────────────


def test_parse_block_multiline():
    text = "rice 90/kg\nonions 40/kg\nmilk 28/litre"
    out = P.parse_price_block(text, currency="INR")
    items = {k for k, _ in out}
    assert items == {"rice", "onions", "milk"}


def test_parse_block_comma_list():
    out = P.parse_price_block("onions 40/kg, tomatoes 30/kg, potato 25/kg",
                              currency="INR")
    items = {k for k, _ in out}
    assert items == {"onions", "tomatoes", "potato"}


def test_parse_block_mixed_lines_and_commas():
    out = P.parse_price_block("rice 90/kg\nonions 40/kg, tomatoes 30/kg",
                              currency="INR")
    assert "rice" in {k for k, _ in out}
    assert "onions" in {k for k, _ in out}
    assert "tomatoes" in {k for k, _ in out}


# ─── merge + format ──────────────────────────────────────────────────────────


def test_merge_preserves_and_overrides():
    base = Prices(entries={"rice": PriceEntry(price=90, unit="kg", per="kg", currency="INR")})
    parsed = P.parse_price_block("rice 80/kg, dal 120/kg", currency="INR")
    merged = P.merge_prices(base, parsed)
    assert merged.entries["rice"].price == 80.0   # overridden
    assert merged.entries["dal"].price == 120.0    # added
    # original not mutated
    assert base.entries["rice"].price == 90.0


def test_merge_case_insensitive_key():
    base = Prices()
    parsed = P.parse_price_block("Rice 90/kg", currency="INR")
    merged = P.merge_prices(base, parsed)
    assert "rice" in merged.entries  # lowercased key


def test_format_prices_empty():
    assert P.format_prices(Prices()) == "(no prices known yet)"


def test_format_prices_nonempty_lists_each():
    prices = Prices(entries={
        "rice": PriceEntry(price=90, unit="kg", per="kg", currency="INR"),
        "milk": PriceEntry(price=28, unit="l", per="l", currency="INR"),
    })
    out = P.format_prices(prices)
    assert "rice" in out
    assert "milk" in out
    assert "90" in out and "28" in out
