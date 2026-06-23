"""Prompt templates for the planner and onboarding/learn LLM calls.

Each builder returns a ``[{role, content}, ...]`` list ready for ``llm.chat``.
The JSON schema is passed separately at the call site (so callers stay free to
reuse schema models from :mod:`src.memory.schema`).

Prompts are deliberately specific about constraints (diet, allergies, budget,
cook-time, prep day, leftover/repetition tolerance, learnings) so the model's
output is grounded in the user's actual memory rather than generic.
"""

from __future__ import annotations

import json
from typing import Iterable

from pydantic import BaseModel

from src.memory.schema import (
    CurrentPlan, FeedbackEntry, Learnings, Pantry, PlanHistoryEntry,
    Preferences, Prices, Profile,
)


def _j(obj) -> str:
    if isinstance(obj, BaseModel):
        return obj.model_dump_json(indent=2, exclude_none=True)
    if isinstance(obj, dict):
        return json.dumps(obj, ensure_ascii=False, indent=2)
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _prices_block(prices: Prices) -> str:
    if not prices.entries:
        return "(no prices known yet — estimate reasonable local prices and note them)"
    lines = []
    for item, e in prices.entries.items():
        lines.append(f"- {item}: {e.currency or ''}{e.price}/{e.per or e.unit}")
    return "\n".join(lines)


def _feedback_block(feedback: Iterable[FeedbackEntry]) -> str:
    rows = list(feedback)
    if not rows:
        return "(no feedback yet)"
    return "\n".join(
        f"- {f.date} {f.meal or ''}: {f.outcome}"
        + (f" — {f.reason}" if f.reason else "")
        + (f" (rating {f.rating}/5)" if f.rating else "")
        for f in rows
    )


def _pantry_block(pantry: Pantry) -> str:
    if not pantry.items:
        return "(pantry empty — plan a full week's groceries)"
    return "\n".join(
        f"- {name}: {pi.qty:g} {pi.unit}"
        for name, pi in pantry.items.items()
    )


# ─── Weekly plan ─────────────────────────────────────────────────────────────

WEEKLY_PLAN_SYSTEM = (
    "You are a meticulous meal-prep planner for a single working professional. "
    "Produce a Monday–Sunday plan as JSON conforming to the CurrentPlan schema.\n\n"
    "Hard constraints — never violate:\n"
    "- diet_type, allergies, religious_or_cultural, hard_dislikes.\n"
    "- cuisines and spice_level preferences.\n"
    "- daily cook-time budget: weekday meals must fit daily_cook_minutes (use "
    "prep_ahead=true meals reheated on busy days; put the real cooking on prep_day).\n"
    "- storage + leftover_tolerance_days: don't plan food to be eaten after it spoils.\n"
    "- repetition_tolerance: don't repeat a dinner more than that allows.\n"
    "- weekly_budget: estimate est_cost from the prices block; if over, swap cheaper "
    "ingredients/meals until within budget.\n"
    "- pantry: meals should USE what's already stocked before buying more. Mark a "
    "grocery line ``in_pantry=true`` when you've planned it from existing stock; "
    "don't add it to the shopping cost in that case.\n\n"
    "For every meal fill: name, slot, ingredients (each with item, qty, unit — use "
    "cooking units like g, cup, pc), steps (concise numbered strings), servings "
    "(= household_size), prep_ahead (true if made on prep day and reheated), "
    "time_minutes (active cook time for the day it's eaten).\n\n"
    "Also fill grocery_list in SHOPPING units (kg, pc, dozen, bunch, packet) with "
    "est_price per line estimated from the prices block, currency, and a boolean "
    "in_pantry only if you're sure it's stocked. Then set est_cost = sum.\n\n"
    "Apply every rule in the learnings block — those are things the user explicitly "
    "told you or that emerged from their feedback. Avoid recent dinners listed in "
    "feedback to keep variety. Output ONLY the JSON object."
)


def build_weekly_plan_messages(
    profile: Profile,
    preferences: Preferences,
    prices: Prices,
    pantry: Pantry,
    learnings: Learnings,
    feedback: Iterable[FeedbackEntry],
    week_of: str,
    dates: list[str],
    household_size: int,
    changes: str = "",
) -> list[dict]:
    change_block = ""
    if changes.strip():
        change_block = (
            f"\nUSER REQUESTED CHANGES to the previous plan — apply these:\n"
            f"{changes.strip()}\n"
        )
    user = (
        f"Plan the week starting Monday {week_of}. Dates: {', '.join(dates)}.\n"
        f"Servings per meal (household size): {household_size}.\n\n"
        f"PROFILE:\n{_j(profile)}\n\n"
        f"PREFERENCES:\n{_j(preferences)}\n\n"
        f"PRICES (local market):\n{_prices_block(prices)}\n\n"
        f"PANTRY (already stocked — favour using these):\n{_pantry_block(pantry)}\n\n"
        f"LEARNINGS (apply these):\n{_j(learnings)}\n\n"
        f"RECENT FEEDBACK (avoid repeating the disliked, lean into the liked):\n"
        f"{_feedback_block(feedback)}\n"
        f"{change_block}\n"
        "Return the CurrentPlan JSON now."
    )
    return [
        {"role": "system", "content": WEEKLY_PLAN_SYSTEM},
        {"role": "user", "content": user},
    ]


# ─── Prep day ────────────────────────────────────────────────────────────────

PREP_DAY_SYSTEM = (
    "You turn a weekly meal plan into an ordered batch-cooking task list for the "
    "user's prep day. Output JSON conforming to the PrepDayPlan schema: a list of "
    "tasks in the order they should be done. Each task: sequence (int, start at 1), "
    "task (short imperative, e.g. 'Pressure-cook 3 portions of dal'), items (the "
    "ingredients/equipment involved), time_minutes (estimated active+wait time), "
    "serves_days (how many days this prep feeds). Group and sequence so long-running "
    "tasks (pressure cooking, soaking, marinating) start first and quick tasks fill "
    "gaps. Output ONLY the JSON."
)


def build_prep_day_messages(plan: CurrentPlan, prep_day_dow: str) -> list[dict]:
    # only meals flagged prep_ahead, plus anything that should be pre-prepped
    user = (
        f"Prep day is {prep_day_dow}. Build the batch-cook task list from this plan.\n"
        f"Focus on meals with prep_ahead=true, plus any washing/chopping/marinating "
        f"that saves weekday time.\n\nPLAN:\n{_j(plan)}\n\nReturn PrepDayPlan JSON."
    )
    return [
        {"role": "system", "content": PREP_DAY_SYSTEM},
        {"role": "user", "content": user},
    ]


# ─── Learn (distill feedback → learnings) ────────────────────────────────────

LEARN_SYSTEM = (
    "You maintain a living set of meal-prep rules for one user. Given recent "
    "feedback and plan history, plus the current learnings, output the UPDATED "
    "Learnings JSON: a list of rules. Each rule: rule (a concrete, actionable "
    "sentence), category (preference|skip|substitution|budget|timing|variety), "
    "confidence (0..1), evidence (short strings citing the feedback that supports "
    "it), updated (today's ISO date).\n\n"
    "Keep rules that still hold, refine them, drop contradicted ones, and add new "
    "ones. Be specific and behavioural ('weekdays: keep breakfast under 10 min — "
    "user skipped cooked breakfasts 3 weeks running') not vague. Output ONLY JSON."
)


def build_learn_messages(
    feedback: list[FeedbackEntry],
    plan_history: list[PlanHistoryEntry],
    current: Learnings,
) -> list[dict]:
    user = (
        f"CURRENT LEARNINGS:\n{_j(current)}\n\n"
        f"RECENT FEEDBACK:\n{_feedback_block(feedback)}\n\n"
        f"PLAN HISTORY:\n{_j(plan_history)}\n\n"
        "Return the updated Learnings JSON."
    )
    return [
        {"role": "system", "content": LEARN_SYSTEM},
        {"role": "user", "content": user},
    ]


# ─── Onboarding: next derived this-or-that question ──────────────────────────

DERIVED_QUESTION_SYSTEM = (
    "You are conducting an adaptive onboarding interview to learn a user's exact "
    "food preferences and local market. You are given the facts already known. "
    "Produce the SINGLE next most-informative question as JSON conforming to the "
    "DerivedQuestion schema: {done, key, question, kind, options, rationale}.\n\n"
    "- kind='choice' with 2–4 options for a this-or-that question (preferred), or "
    "  'multichoice' for pick-any, or 'text' only when you need a free answer "
    "  (e.g. listing staple prices).\n"
    "- Ask about specifics that branch on what's already known: e.g. if diet=veg, "
    "  ask paneer-vs-tofu; if budget is low, ask rice-vs-roti staple; if cook time "
    "  is low, ask one-pot-ok and same-lunch-3-days-ok; if no oven, skip baked "
    "  dishes; ask breakfast style, leftover tolerance, repetition tolerance.\n"
    "- Don't re-ask anything already answered (see answered_keys).\n"
    "- Set done=true ONLY when you have enough to plan a varied week that fits "
    "  their budget, diet, cook-time, prep day, repetition and leftover tolerance "
    "  AND their staple prices are known. Otherwise done=false.\n"
    "Output ONLY the JSON."
)


def build_derived_question_messages(
    profile: Profile,
    preferences: Preferences,
    prices: Prices,
    answered_keys: list[str],
) -> list[dict]:
    user = (
        f"PROFILE (preliminary answers):\n{_j(profile)}\n\n"
        f"PREFERENCES so far:\n{_j(preferences)}\n\n"
        f"PRICES known:\n{_prices_block(prices)}\n\n"
        f"ALREADY ANSWERED (do not repeat): {answered_keys}\n\n"
        "Return the next DerivedQuestion JSON."
    )
    return [
        {"role": "system", "content": DERIVED_QUESTION_SYSTEM},
        {"role": "user", "content": user},
    ]


# ─── Grocery trim to budget ──────────────────────────────────────────────────

TRIM_SYSTEM = (
    "A weekly grocery list exceeds the user's budget. Return a REVISED grocery_list "
    "as JSON: a list of {item, qty, unit, est_price, currency, in_pantry, note}. "
    "Cut cost to within budget by swapping to cheaper ingredients, reducing "
    "quantities sensibly, or dropping luxuries — but keep enough food for the "
    "household for the week. Don't remove essentials. Output ONLY the JSON array "
    "object: {\"grocery_list\": [...], \"est_cost\": number}."
)


def build_grocery_trim_messages(plan: CurrentPlan, prices: Prices, budget: float, over_by: float) -> list[dict]:
    user = (
        f"Budget: {plan.currency or ''}{budget}. Current est_cost is over by "
        f"{plan.currency or ''}{over_by:.0f}.\n\n"
        f"PRICES:\n{_prices_block(prices)}\n\n"
        f"CURRENT GROCERY LIST:\n{_j(plan.grocery_list)}\n\n"
        "Return the trimmed {grocery_list, est_cost} JSON."
    )
    return [
        {"role": "system", "content": TRIM_SYSTEM},
        {"role": "user", "content": user},
    ]


REVISE_SYSTEM = (
    "The user wants to change their grocery list. Apply their request to the "
    "current list and return the REVISED list as JSON: "
    "{\"grocery_list\": [{item, qty, unit, est_price, currency, in_pantry, note}], "
    "\"est_cost\": number}. Honour adds, removes, and quantity changes. Re-estimate "
    "est_price from the prices block where possible. Output ONLY the JSON."
)


def build_grocery_revise_messages(plan: CurrentPlan, prices: Prices, note: str) -> list[dict]:
    user = (
        f"USER REQUEST: {note.strip()}\n\n"
        f"PRICES:\n{_prices_block(prices)}\n\n"
        f"CURRENT GROCERY LIST:\n{_j(plan.grocery_list)}\n\n"
        "Return the revised {grocery_list, est_cost} JSON."
    )
    return [
        {"role": "system", "content": REVISE_SYSTEM},
        {"role": "user", "content": user},
    ]
