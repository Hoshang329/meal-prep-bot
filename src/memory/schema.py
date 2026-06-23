"""Pydantic models for every memory document stored in the Discord memory channel.

Design notes:
- Fields are ``Optional`` with defaults so docs validate while onboarding is
  still filling them in, and so AI-produced JSON that omits optional fields is
  accepted. ``extra="ignore"`` tolerates extra LLM-invented keys gracefully.
- Dates/timestamps are ISO strings (portable, human-readable in the channel).
- A ``DOC_MODELS`` registry maps doc-name -> model so the store can give typed
  objects back: ``store.get_doc(Profile)`` returns a ``Profile``.
- Append-only logs (feedback, plan_history) have their own entry models and are
  stored as tagged text messages, not as editable docs.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    """Common config: ignore unknown keys (AI JSON is messy), allow populate by name."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# ─── Ingredients / meals / the weekly plan ───────────────────────────────────


class Ingredient(_Base):
    item: str
    qty: Optional[float] = None
    unit: Optional[str] = None  # e.g. "kg", "g", "pc", "cup", "tbsp"


class Meal(_Base):
    name: str
    slot: Optional[str] = None  # breakfast | lunch | dinner | snack
    ingredients: list[Ingredient] = []
    steps: list[str] = []
    servings: Optional[float] = None
    prep_ahead: bool = False  # made on prep day, reheated later
    reheat: bool = False
    tags: list[str] = []
    time_minutes: Optional[int] = None
    notes: Optional[str] = None


class DayPlan(_Base):
    date: str  # ISO date
    dow: Optional[str] = None  # Monday..Sunday
    breakfast: Optional[Meal] = None
    lunch: Optional[Meal] = None
    dinner: Optional[Meal] = None
    snacks: list[Meal] = []
    is_prep_ahead: bool = False
    notes: Optional[str] = None


class GroceryItem(_Base):
    item: str
    qty: float
    unit: str
    est_price: Optional[float] = None
    currency: Optional[str] = None
    in_pantry: bool = False  # already stocked -> don't need to buy
    note: Optional[str] = None


class CurrentPlan(_Base):
    week_of: str  # ISO date of the plan's Monday
    days: list[DayPlan] = []
    grocery_list: list[GroceryItem] = []
    est_cost: Optional[float] = None
    currency: Optional[str] = None
    budget: Optional[float] = None
    status: Literal["draft", "approved", "prepping", "done"] = "draft"
    generated_at: Optional[str] = None
    approved_at: Optional[str] = None
    notes: Optional[str] = None


# ─── Onboarding docs ─────────────────────────────────────────────────────────


class Profile(_Base):
    name: Optional[str] = None
    location: Optional[str] = None  # city / region
    currency: Optional[str] = None  # INR, USD, ...
    household_size: Optional[int] = None  # people cooked for
    weekly_budget: Optional[float] = None
    diet_type: Optional[str] = None  # veg | non-veg | eggetarian | vegan | jain | ...
    allergies: list[str] = []
    religious_or_cultural: list[str] = []
    skill: Optional[str] = None  # none | basic | comfortable | advanced
    equipment: list[str] = []  # stove, oven, microwave, air-fryer, instant-pot, ...
    daily_cook_minutes: Optional[int] = None
    prep_day: Optional[str] = None  # Sunday | Saturday | ...
    meals_per_day: Optional[int] = None
    meal_times: dict = {}  # {"breakfast": "08:00", "lunch": "13:00", ...}
    storage: Optional[str] = None  # fridge | fridge+freezer
    health_goal: Optional[str] = None  # bulk | cut | maintain | medical | ...
    cuisines: list[str] = []
    spice_level: Optional[str] = None  # mild | medium | hot
    hard_dislikes: list[str] = []
    updated: Optional[str] = None


class Preferences(_Base):
    staple_grain: Optional[str] = None
    preferred_proteins: list[str] = []
    breakfast_style: Optional[str] = None  # quick | cooked | minimal
    lunch_style: Optional[str] = None
    dinner_style: Optional[str] = None
    one_pot_ok: Optional[bool] = None
    leftover_tolerance_days: Optional[int] = None
    repetition_tolerance: Optional[str] = None  # low | medium | high
    this_or_that_votes: dict = {}  # {question_key: chosen_option}
    updated: Optional[str] = None


# ─── Prices / pantry ─────────────────────────────────────────────────────────


class PriceEntry(_Base):
    price: float
    currency: Optional[str] = None
    unit: str  # the unit the item is sold/measured in
    per: Optional[str] = None  # the unit the price is for (usually == unit)
    updated: Optional[str] = None  # ISO date
    note: Optional[str] = None  # e.g. "local kirana", "supermarket"


class Prices(_Base):
    entries: dict[str, PriceEntry] = {}


class PantryItem(_Base):
    qty: float
    unit: str


class Pantry(_Base):
    items: dict[str, PantryItem] = {}


# ─── Recipe library (AI cache) ───────────────────────────────────────────────


class Recipe(_Base):
    id: str
    name: str
    slot: Optional[str] = None
    ingredients: list[Ingredient] = []
    steps: list[str] = []
    servings: Optional[float] = None
    tags: list[str] = []
    rating: Optional[int] = None  # 1..5
    created_at: Optional[str] = None


class RecipeLibrary(_Base):
    recipes: list[Recipe] = []


# ─── Learnings (distilled feedback) ──────────────────────────────────────────


class Learning(_Base):
    rule: str
    category: Optional[str] = None  # preference | skip | substitution | budget | timing
    confidence: Optional[float] = None  # 0..1
    evidence: list[str] = []
    updated: Optional[str] = None


class Learnings(_Base):
    rules: list[Learning] = []
    updated: Optional[str] = None


# ─── Onboarding progress ─────────────────────────────────────────────────────


class OnboardingState(_Base):
    phase: Literal["preliminary", "derived", "done"] = "preliminary"
    preliminary_index: int = 0
    answered: dict = {}  # {question_key: value}
    pending_question: Optional[dict] = None  # currently-asked derived question
    prices_collected: bool = False
    started_at: Optional[str] = None
    updated: Optional[str] = None


# ─── Owner lock (persisted across restarts) ──────────────────────────────────


class OwnerDoc(_Base):
    """Records which Discord account is allowed to command the bot.

    Only written on first DM when ``OWNER_DISCORD_ID`` env is not set; if the env
    is set, the env wins permanently. Lives in the memory channel so the lock
    survives restarts (the old in-memory ``ctx.owner_id`` was reset on every
    restart, allowing the first person to DM after a restart to hijack it).
    """

    discord_id: Optional[int] = None
    claimed_at: Optional[str] = None


# ─── Append-only log entry models ────────────────────────────────────────────


class FeedbackEntry(_Base):
    date: str  # ISO date the feedback is about
    day_index: Optional[int] = None
    week_of: Optional[str] = None
    outcome: Literal["cooked", "other", "skipped"]
    meal: Optional[str] = None  # breakfast | lunch | dinner | snacks
    reason: Optional[str] = None
    rating: Optional[int] = None  # 1..5
    logged_at: Optional[str] = None


class PlanHistoryEntry(_Base):
    week_of: str
    status: str
    summary: Optional[str] = None
    rating: Optional[int] = None
    cost: Optional[float] = None
    logged_at: Optional[str] = None


# ─── Registry: doc name -> model ─────────────────────────────────────────────
# The store uses this to (de)serialise the structured docs. Logs are NOT here —
# they are append-only and handled separately.

DOC_MODELS: dict[str, type[BaseModel]] = {
    "profile": Profile,
    "preferences": Preferences,
    "prices": Prices,
    "pantry": Pantry,
    "current_plan": CurrentPlan,
    "recipe_library": RecipeLibrary,
    "learnings": Learnings,
    "onboarding_state": OnboardingState,
    "owner": OwnerDoc,
}

LOG_MODELS: dict[str, type[BaseModel]] = {
    "feedback": FeedbackEntry,
    "plan_history": PlanHistoryEntry,
}

# Log entry models keyed by kind, used by the store for typed log reads.
__all__ = [
    "Ingredient", "Meal", "DayPlan", "GroceryItem", "CurrentPlan",
    "Profile", "Preferences", "PriceEntry", "Prices", "PantryItem", "Pantry",
    "Recipe", "RecipeLibrary", "Learning", "Learnings", "OnboardingState",
    "OwnerDoc", "FeedbackEntry", "PlanHistoryEntry", "DOC_MODELS", "LOG_MODELS",
]
