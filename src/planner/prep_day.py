"""Prep-day task list: LLM turns the week's prep-ahead meals into an ordered batch-cook plan."""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, ConfigDict, ValidationError

from src.memory.schema import CurrentPlan
from src.planner import llm, prompts

log = logging.getLogger(__name__)


class PrepTask(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sequence: int
    task: str
    items: list[str] = []
    time_minutes: Optional[int] = None
    serves_days: Optional[int] = None


class PrepDayPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")
    prep_day: Optional[str] = None
    total_time_minutes: Optional[int] = None
    tasks: list[PrepTask] = []


async def generate(plan: CurrentPlan, prep_day_dow: str) -> PrepDayPlan:
    messages = prompts.build_prep_day_messages(plan, prep_day_dow)
    data = await llm.chat_json(messages, PrepDayPlan, temperature=0.5)
    try:
        p = PrepDayPlan.model_validate(data)
    except ValidationError as e:
        raise llm.LLMError(f"Prep-day plan did not match schema: {e}") from e
    p.prep_day = p.prep_day or prep_day_dow
    # ensure tasks are sorted by sequence
    p.tasks.sort(key=lambda t: t.sequence)
    return p


def format(prep: PrepDayPlan) -> str:
    if not prep.tasks:
        return "🍳 Nothing to batch-prep this week — daily cooking only."
    head = "🍳 **Prep-day task list**"
    if prep.total_time_minutes:
        head += f" (≈ {prep.total_time_minutes} min total)"
    head += ":"
    lines = [head]
    for t in prep.tasks:
        time_s = f" ({t.time_minutes} min)" if t.time_minutes else ""
        serves = f" — feeds {t.serves_days} days" if t.serves_days else ""
        lines.append(f"{t.sequence}. {t.task}{time_s}{serves}")
    lines.append("\nStart the long-running tasks first; fill gaps with chopping/marinating.")
    return "\n".join(lines)
