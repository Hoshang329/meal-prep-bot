"""Unit tests for memory schema validation (round-trip, defaults, extra-ignore)."""

import pytest
from pydantic import ValidationError

from src.memory.schema import (
    CurrentPlan, DayPlan, FeedbackEntry, GroceryItem, Meal,
    OnboardingState, Preferences, Profile, RecipeLibrary,
)


# ─── round-trip ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("model", [
    Profile(), Preferences(), CurrentPlan(week_of="2026-06-22"),
    OnboardingState(), RecipeLibrary(),
])
def test_model_roundtrip(model):
    data = model.model_dump(mode="json")
    again = type(model).model_validate(data)
    assert again == model


def test_profile_roundtrip_with_data():
    p = Profile(location="Pune", currency="INR", household_size=2,
                weekly_budget=2000, diet_type="veg",
                allergies=["peanuts"], equipment=["stove", "instant-pot"],
                cuisines=["indian"], spice_level="medium")
    again = Profile.model_validate(p.model_dump(mode="json"))
    assert again == p
    assert again.household_size == 2
    assert again.equipment == ["stove", "instant-pot"]


# ─── defaults ─────────────────────────────────────────────────────────────────


def test_current_plan_defaults():
    p = CurrentPlan(week_of="2026-06-22")
    assert p.status == "draft"
    assert p.days == []
    assert p.grocery_list == []
    assert p.est_cost is None


def test_onboarding_state_defaults():
    st = OnboardingState()
    assert st.phase == "preliminary"
    assert st.preliminary_index == 0
    assert st.prices_collected is False


def test_optional_fields_default_none():
    m = Meal(name="dal")
    assert m.slot is None
    assert m.servings is None
    assert m.prep_ahead is False
    assert m.ingredients == []
    assert m.steps == []


# ─── extra keys ignored (AI JSON is messy) ────────────────────────────────────


def test_extra_keys_ignored():
    p = Profile.model_validate({"location": "Pune", "bogus_field": 123, "weather": "rainy"})
    assert p.location == "Pune"


def test_current_plan_ignores_extra():
    p = CurrentPlan.model_validate({"week_of": "2026-06-22", "llm_confidence": 0.9})
    assert p.week_of == "2026-06-22"


# ─── required fields + literals ───────────────────────────────────────────────


def test_current_plan_requires_week_of():
    with pytest.raises(ValidationError):
        CurrentPlan.model_validate({})  # week_of is required


def test_feedback_entry_requires_outcome():
    with pytest.raises(ValidationError):
        FeedbackEntry.model_validate({"date": "2026-06-22"})  # missing outcome


def test_feedback_entry_validates_outcome_literal():
    e = FeedbackEntry.model_validate({"date": "2026-06-22", "outcome": "cooked"})
    assert e.outcome == "cooked"
    with pytest.raises(ValidationError):
        FeedbackEntry.model_validate({"date": "2026-06-22", "outcome": "bogus"})


def test_current_plan_status_literal():
    p = CurrentPlan.model_validate({"week_of": "2026-06-22", "status": "approved"})
    assert p.status == "approved"
    with pytest.raises(ValidationError):
        CurrentPlan.model_validate({"week_of": "2026-06-22", "status": "bogus"})


# ─── nested validation ────────────────────────────────────────────────────────


def test_day_plan_with_meals_validates():
    d = DayPlan.model_validate({
        "date": "2026-06-22", "dow": "Monday",
        "breakfast": {"name": "poha", "slot": "breakfast", "prep_ahead": False},
        "lunch": {"name": "dal-chawal", "slot": "lunch"},
    })
    assert d.breakfast.name == "poha"
    assert d.lunch.slot == "lunch"
    assert d.dinner is None


def test_grocery_item_qty_required():
    with pytest.raises(ValidationError):
        GroceryItem.model_validate({"item": "rice", "unit": "kg"})  # missing qty
    g = GroceryItem.model_validate({"item": "rice", "qty": 2, "unit": "kg"})
    assert g.qty == 2.0
