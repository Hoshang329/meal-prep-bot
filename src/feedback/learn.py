"""Learn job: distil recent feedback + plan history into updated learnings.

The output ``Learnings`` is consumed by the next weekly plan, so memory improves
week over week. Run periodically (e.g. nightly) and on demand via /learn.
"""

from __future__ import annotations

import logging

from src.app import ctx
from src.memory.schema import Learnings
from src.planner import llm, prompts
from src.util.dt import today_iso

log = logging.getLogger(__name__)


async def run_learn() -> Learnings:
    ctx.ensure()
    feedback = await ctx.store.get_logs("feedback", limit=40)
    plan_history = await ctx.store.get_logs("plan_history", limit=12)
    current = await ctx.store.get_or_default(Learnings)

    messages = prompts.build_learn_messages(feedback, plan_history, current)
    data = await llm.chat_json(messages, Learnings, temperature=0.4)
    try:
        learned = Learnings.model_validate(data)
    except Exception as e:
        log.error("Learn output failed validation: %s", e)
        return current
    learned.updated = today_iso()
    await ctx.store.set(learned)
    log.info("Learnings updated: %d rules", len(learned.rules))
    return learned


def format(learned: Learnings) -> str:
    if not learned.rules:
        return "_(no learnings yet — they accumulate from your feedback)_"
    lines = [f"🧠 **Learnings ({len(learned.rules)} rules):**"]
    for r in learned.rules:
        cat = f" [{r.category}]" if r.category else ""
        conf = f" ({r.confidence:.0%})" if r.confidence is not None else ""
        lines.append(f"• {r.rule}{cat}{conf}")
    return "\n".join(lines)
