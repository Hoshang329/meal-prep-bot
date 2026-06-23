"""Grocery finalisation: price-check the LLM's list, apply pantry, compare to budget.

The weekly-plan LLM already proposes a shopping-unit grocery list with estimated
prices. This module is the *verifier/adjuster*: it re-estimates each line from the
user's real ``prices.json`` where possible, flags pantry items, computes the total,
and trims to budget via an LLM call if over.

Every LLM call here uses a strict JSON schema (a small Pydantic model) so the
OpenAI-compatible ``response_format={"type":"json_object"}`` mode is enabled and
``llm.chat`` enforces the JSON — previously these calls dispatched without a schema
and silently relied on the model obeying "Output ONLY JSON", which is fragile.
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from src.memory.schema import CurrentPlan, GroceryItem, Pantry, Prices
from src.planner import llm, prompts
from src.util import units

log = logging.getLogger(__name__)


class _GroceryRevision(BaseModel):
    """Shape returned by the trim/revise LLM calls: a list + total."""
    # ``extra="ignore"`` keeps model-invented extras from breaking validation.
    model_config = ConfigDict(extra="ignore")

    grocery_list: list[GroceryItem] = Field(default_factory=list)
    est_cost: Optional[float] = None


def over_budget(plan: CurrentPlan) -> Optional[float]:
    """Return how much the plan exceeds its budget, or None if within/no budget."""
    if plan.budget is None or plan.est_cost is None:
        return None
    return max(0.0, plan.est_cost - plan.budget)


def finalize(plan: CurrentPlan, prices: Prices, pantry: Pantry) -> CurrentPlan:
    """Re-price each grocery line from ``prices``, mark pantry stock, recompute total."""
    pantry_items = {k.strip().lower() for k in pantry.items.keys()}
    total = 0.0
    any_priced = False
    for g in plan.grocery_list:
        if g.item.strip().lower() in pantry_items:
            g.in_pantry = True
        cost, note = units.estimate_cost(g.item, g.qty, g.unit, prices, plan.currency)
        if cost is not None:
            g.est_price = cost
            g.currency = g.currency or plan.currency
            any_priced = True
            total += cost
        elif g.est_price is not None:
            # keep the LLM's estimate, but note we couldn't verify
            total += g.est_price
            any_priced = True
        if note and not g.note:
            g.note = note
    plan.est_cost = round(total, 2) if any_priced else plan.est_cost
    return plan


async def trim_to_budget(plan: CurrentPlan, prices: Prices, pantry: Pantry) -> CurrentPlan:
    """If over budget, ask the LLM to trim the grocery list; re-finalize (re-flag pantry)."""
    over = over_budget(plan)
    if over is None or over <= 0 or plan.budget is None:
        return plan
    messages = prompts.build_grocery_trim_messages(plan, prices, plan.budget, over)
    data = await llm.chat_json(messages, _GroceryRevision, temperature=0.5)
    return _apply_revision(plan, data, prices, pantry)


async def finalize_and_trim(plan: CurrentPlan, prices: Prices, pantry: Pantry) -> CurrentPlan:
    """Price the LLM's grocery list from real prices, then trim to budget if needed."""
    plan = finalize(plan, prices, pantry)
    plan = await trim_to_budget(plan, prices, pantry)
    return plan


async def revise(plan: CurrentPlan, prices: Prices, pantry: Pantry, note: str) -> CurrentPlan:
    """Apply a user's free-text change request to the grocery list, then re-finalize."""
    messages = prompts.build_grocery_revise_messages(plan, prices, note)
    data = await llm.chat_json(messages, _GroceryRevision, temperature=0.4)
    return _apply_revision(plan, data, prices, pantry)


def _apply_revision(plan: CurrentPlan, data: dict, prices: Prices, pantry: Pantry) -> CurrentPlan:
    """Replace the grocery_list from a trim/revise LLM call and re-finalize."""
    # validate via the model so the schema's tolerances (extra=ignore, optional
    # fields, default_factory for missing lists) all apply.
    rev = _GroceryRevision.model_validate(data)
    plan.grocery_list = list(rev.grocery_list)
    if rev.est_cost is not None:
        plan.est_cost = rev.est_cost
    return finalize(plan, prices, pantry)


def format(plan: CurrentPlan) -> str:
    """Human-readable grocery list + total vs budget."""
    if not plan.grocery_list:
        return "🛒 Grocery list is empty."
    cur = plan.currency or ""
    lines = ["🛒 **Grocery list for the week:**"]
    for g in plan.grocery_list:
        tag = " *(in pantry)*" if g.in_pantry else ""
        price = f" — {cur}{g.est_price:.0f}" if g.est_price is not None else ""
        lines.append(f"• {g.qty:g} {g.unit} {g.item}{price}{tag}")
    if plan.est_cost is not None:
        lines.append(f"\n**Estimated total: {cur}{plan.est_cost:.0f}**")
    if plan.budget is not None:
        if plan.est_cost is not None and plan.est_cost > plan.budget:
            lines.append(f"⚠ Over your {cur}{plan.budget:.0f} budget by "
                         f"{cur}{plan.est_cost - plan.budget:.0f}.")
        else:
            lines.append(f"Within your {cur}{plan.budget:.0f} budget. ✅")
    return "\n".join(lines)
