"""Preliminary onboarding questions — a fixed script of the must-have facts.

Each question is rendered as inline buttons (choice / multichoice) or a free-text
prompt (text / number). Answers are applied to the ``Profile`` memory doc.

The script is resumable: ``flow.py`` stores ``preliminary_index`` in
``onboarding_state`` so a restart picks up where the user stopped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Union

from src.memory.schema import Profile


@dataclass
class Question:
    key: str
    prompt: str
    kind: str  # choice | multichoice | text | number
    options: list[str] = field(default_factory=list)
    field: str = ""          # Profile attribute to set (defaults to key)
    is_list: bool = False    # store as a list
    optional: bool = False
    help: str = ""

    def __post_init__(self):
        if not self.field:
            self.field = self.key


PRELIMINARY: list[Question] = [
    Question("location", "Where are you (city/area)? This helps tailor the market context.", "text"),
    Question("currency", "What currency do you use? (e.g. INR, USD, EUR)", "text"),
    Question("household_size", "How many people are you cooking for?", "number"),
    Question("weekly_budget", "What's your weekly food budget? (just the number)", "number"),
    Question("diet_type", "What's your diet?", "choice",
             options=["Vegetarian", "Non-vegetarian", "Eggetarian", "Vegan", "Jain"]),
    Question("religious_or_cultural", "Any religious or cultural food rules? (comma-separated, or 'none')",
             "text", is_list=True, optional=True),
    Question("allergies", "Any allergies? (comma-separated, or 'none')", "text", is_list=True, optional=True),
    Question("skill", "How would you rate your cooking?", "choice",
             options=["Can't cook", "Basic", "Comfortable", "Advanced"]),
    Question("equipment", "Which equipment do you have?", "multichoice",
             options=["Stove", "Oven", "Microwave", "Air fryer", "Instant Pot",
                      "Blender", "Rice cooker", "None"],
             is_list=True),
    Question("daily_cook_minutes", "Max minutes you can cook per weekday?", "number"),
    Question("prep_day", "Which day is your batch-prep day?", "choice",
             options=["Sunday", "Saturday", "Friday", "Monday", "No prep day"]),
    Question("meals_per_day", "How many meals per day? (1–5)", "number"),
    Question("meal_times", "When do you eat? e.g. 'breakfast 8, lunch 1, dinner 8'",
             "text", optional=True),
    Question("storage", "What storage do you have?", "choice",
             options=["Fridge only", "Fridge + freezer"]),
    Question("health_goal", "Any health/fitness goal?", "choice",
             options=["Maintain", "Bulk", "Cut", "Medical", "No specific goal"]),
    Question("cuisines", "Which cuisines do you like? (pick all)", "multichoice",
             options=["Indian", "Italian", "Asian", "Mexican", "Middle Eastern",
                      "Continental", "American", "Other"],
             is_list=True),
    Question("spice_level", "Spice tolerance?", "choice",
             options=["Mild", "Medium", "Hot"]),
    Question("hard_dislikes", "Ingredients you really dislike? (comma-separated, or 'none')",
             "text", is_list=True, optional=True),
]


# ─── answer parsing & application ────────────────────────────────────────────

_NONE_TOKENS = {"none", "na", "n/a", "nothing", "nil", "-", ""}


def _parse_list(text: str) -> list[str]:
    if not text:
        return []
    parts = [p.strip().lower() for p in re.split(r"[,\n]+", text)]
    return [p for p in parts if p and p not in _NONE_TOKENS]


def _parse_number(text: str, *, prefer_int: bool = True) -> Optional[Union[int, float]]:
    m = re.search(r"\d+(?:\.\d+)?", text.replace(",", ""))
    if not m:
        return None
    n = float(m.group())
    if prefer_int and n.is_integer():
        return int(n)
    return n


_TIME_KEYS = {
    "breakfast": "breakfast", "morning": "breakfast",
    "lunch": "lunch", "afternoon": "lunch",
    "dinner": "dinner", "evening": "dinner", "supper": "dinner",
    "snack": "snacks", "snacks": "snacks", "tea": "snacks",
}


def _parse_meal_times(text: str) -> dict:
    out: dict[str, str] = {}
    for part in re.split(r"[,\n]+", text):
        m = re.match(r"\s*([a-zA-Z]+)\D{0,3}(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", part, re.I)
        if not m:
            continue
        word, hh, mm, ap = m.group(1).lower(), int(m.group(2)), m.group(3), (m.group(4) or "").lower()
        slot = _TIME_KEYS.get(word)
        if not slot:
            continue
        h = hh
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        out[slot] = f"{h:02d}:{mm or '00'}"
    return out


def apply_preliminary_answer(profile: Profile, q: Question, value) -> None:
    """Mutate ``profile`` with ``value`` (a string for text/number, str for choice, list for multichoice)."""
    if q.kind == "multichoice":
        setattr(profile, q.field, list(value) if isinstance(value, (list, tuple)) else [value])
        return

    if q.kind == "choice":
        setattr(profile, q.field, value)
        return

    # text / number
    text = str(value).strip()
    if q.is_list:
        items = _parse_list(text)
        setattr(profile, q.field, items)
        return

    if q.field == "meal_times":
        setattr(profile, q.field, _parse_meal_times(text))
        return

    if q.kind == "number":
        num = _parse_number(text, prefer_int=(q.field != "weekly_budget"))
        if num is not None:
            setattr(profile, q.field, num)
        return

    # plain text field
    setattr(profile, q.field, text)
