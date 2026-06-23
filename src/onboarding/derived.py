"""Derived onboarding: LLM-generated adaptive this-or-that questions.

After the preliminary script, the LLM proposes the next most-informative question
given everything already known, until it judges the profile sufficient (``done``).
A dedicated prices sub-step runs first to seed ``prices.json``.

All derived answers are recorded in ``preferences.this_or_that_votes`` so the
weekly planner can read them as concrete preferences.
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, ConfigDict, ValidationError

from src.memory.schema import Preferences, Prices, Profile
from src.planner import llm, prompts
from src.util import prices as prices_util

log = logging.getLogger(__name__)


class DerivedQuestion(BaseModel):
    model_config = ConfigDict(extra="ignore")
    done: bool = False
    key: str = ""
    question: str = ""
    kind: str = "choice"  # choice | multichoice | text
    options: list[str] = []
    rationale: str = ""


PRICES_PROMPT = (
    "🛒 Now let's build your local market price list — this is what makes the "
    "grocery lists and budget checks accurate.\n\n"
    "List 5–10 staples you always buy, each as `item price/unit`, separated by "
    "commas or new lines. For example:\n"
    "    rice 80/kg, onions 40/kg, tomatoes 30/kg, milk 28/litre, eggs 6/dozen, "
    "potato 30/kg, chicken 220/kg\n\n"
    "You can add or correct any price later with `/price rice 90/kg`."
)


async def next_question(
    profile: Profile,
    preferences: Preferences,
    prices: Prices,
    answered_keys: list[str],
) -> DerivedQuestion:
    messages = prompts.build_derived_question_messages(profile, preferences, prices, answered_keys)
    data = await llm.chat_json(messages, DerivedQuestion, temperature=0.6)
    try:
        return DerivedQuestion.model_validate(data)
    except ValidationError as e:
        raise llm.LLMError(f"Derived question did not match schema: {e}") from e


def apply_derived_answer(preferences: Preferences, q: DerivedQuestion, value) -> None:
    """Record the answer in this_or_that_votes (value is str, list, or raw text)."""
    if not q.key:
        return
    if isinstance(value, (list, tuple)):
        preferences.this_or_that_votes[q.key] = list(value)
    else:
        preferences.this_or_that_votes[q.key] = value
    preferences.updated = _today()


def apply_prices_answer(prices: Prices, text: str, currency: Optional[str]) -> Prices:
    """Parse a free-text price block and merge into ``prices``."""
    parsed = prices_util.parse_price_block(text, currency=currency)
    return prices_util.merge_prices(prices, parsed)


def _today() -> str:
    from src.util.dt import today_iso
    return today_iso()
