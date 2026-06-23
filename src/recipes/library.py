"""Recipe library cache.

When a plan is approved, its meals are extracted into ``recipe_library`` so future
plans can reuse them (fewer LLM calls, more consistency). Also supports rating a
recipe from feedback ("that dinner was great" → bump rating).
"""

from __future__ import annotations

import logging
from typing import Optional

from src.app import ctx
from src.memory.schema import CurrentPlan, Meal, Recipe, RecipeLibrary

log = logging.getLogger(__name__)


def _meal_to_recipe(meal: Meal) -> Recipe:
    # stable-ish id from name
    rid = meal.name.strip().lower().replace(" ", "_")[:40]
    return Recipe(
        id=rid, name=meal.name, slot=meal.slot,
        ingredients=meal.ingredients, steps=meal.steps,
        servings=meal.servings, tags=meal.tags,
        created_at=_today(),
    )


def _today() -> str:
    from src.util.dt import today_iso
    return today_iso()


async def ingest_plan(plan: CurrentPlan) -> RecipeLibrary:
    """Add all meals in an approved plan to the library (dedup by id).

    On id collision we OVERWRITE with the newer version — a later plan that
    improves ingredients/steps should win, not the early first one (the previous
    first-wins behaviour left stale, worse recipes in the library forever).
    """
    lib = await ctx.store.get_or_default(RecipeLibrary)
    by_id = {r.id: r for r in lib.recipes}
    for day in plan.days:
        for meal in (day.breakfast, day.lunch, day.dinner, *day.snacks):
            if not meal or not meal.name:
                continue
            r = _meal_to_recipe(meal)
            by_id[r.id] = r  # latest version wins
    lib.recipes = list(by_id.values())
    await ctx.store.set(lib)
    return lib


async def rate(recipe_id: str, rating: int) -> Optional[Recipe]:
    lib = await ctx.store.get_or_default(RecipeLibrary)
    for r in lib.recipes:
        if r.id == recipe_id:
            r.rating = max(1, min(5, int(rating)))
            await ctx.store.set(lib)
            return r
    return None


async def list_all() -> RecipeLibrary:
    return await ctx.store.get_or_default(RecipeLibrary)


def format(lib: RecipeLibrary, limit: int = 20) -> str:
    if not lib.recipes:
        return "_(recipe library is empty — it fills as you approve weekly plans)_"
    lines = [f"📖 **Recipe library ({len(lib.recipes)}):**"]
    for r in lib.recipes[:limit]:
        stars = "⭐" * (r.rating or 0) if r.rating else ""
        slot = f" [{r.slot}]" if r.slot else ""
        lines.append(f"• {r.name}{slot} {stars}")
    if len(lib.recipes) > limit:
        lines.append(f"_…and {len(lib.recipes) - limit} more_")
    return "\n".join(lines)
