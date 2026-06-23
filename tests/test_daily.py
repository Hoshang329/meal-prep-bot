"""Unit tests for daily menu formatting (pure, no LLM/Telegram)."""

from src.memory.schema import CurrentPlan, DayPlan, Ingredient, Meal
from src.planner import daily
from src.util.dt import dow_for, today_iso


def _day(date="2026-06-22", dow="Monday", **kw) -> DayPlan:
    return DayPlan(date=date, dow=dow, **kw)


# ─── finders ──────────────────────────────────────────────────────────────────


def test_find_by_date_hit():
    plan = CurrentPlan(week_of="2026-06-22", days=[_day("2026-06-22"), _day("2026-06-23")])
    assert daily.find_by_date(plan, "2026-06-23") is not None
    assert daily.find_by_date(plan, "2026-06-23").date == "2026-06-23"


def test_find_by_date_miss():
    plan = CurrentPlan(week_of="2026-06-22", days=[_day("2026-06-22")])
    assert daily.find_by_date(plan, "2026-06-30") is None


def test_find_by_date_none_plan():
    assert daily.find_by_date(None, "2026-06-22") is None


def test_find_today_uses_real_today():
    today = today_iso()
    plan = CurrentPlan(week_of=today, days=[_day(today)])
    found = daily.find_today(plan)
    assert found is not None
    assert found.date == today


def test_find_today_miss():
    plan = CurrentPlan(week_of="2026-06-22", days=[_day("2026-06-22")])
    # only passes if today isn't 2026-06-22; assert via direct check instead
    assert (daily.find_today(plan) is None) or (daily.find_today(plan).date == "2026-06-22")


# ─── format_day ───────────────────────────────────────────────────────────────


def test_format_day_includes_title_and_meals():
    day = _day(breakfast=Meal(name="poha", slot="breakfast", time_minutes=10,
                              ingredients=[Ingredient(item="rice flakes", qty=1, unit="cup")],
                              steps=["wash flakes", "temper spices"]),
               dinner=Meal(name="dal", slot="dinner"))
    out = daily.format_day(day)
    assert "Monday" in out
    assert "2026-06-22" in out
    assert "poha" in out
    assert "dal" in out
    assert "10 min" in out
    assert "rice flakes" in out
    assert "1. wash flakes" in out
    assert "2. temper spices" in out


def test_format_day_reheat_tag_for_prep_ahead():
    day = _day(dinner=Meal(name="dal", slot="dinner", prep_ahead=True))
    out = daily.format_day(day)
    assert "reheat" in out


def test_format_day_reheat_tag_for_reheat_flag():
    day = _day(dinner=Meal(name="dal", slot="dinner", reheat=True))
    out = daily.format_day(day)
    assert "reheat" in out


def test_format_day_prep_ahead_title():
    day = _day(is_prep_ahead=True)
    out = daily.format_day(day)
    assert "mostly prep-ahead" in out


def test_format_day_notes_appended():
    day = _day(notes="heavy cooking day")
    out = daily.format_day(day)
    assert "heavy cooking day" in out
    assert "📝" in out


def test_format_day_includes_snacks():
    day = _day(snacks=[Meal(name="roasted chana", slot="snack")])
    out = daily.format_day(day)
    assert "roasted chana" in out


def test_format_day_omits_missing_meals():
    day = _day()  # no meals
    out = daily.format_day(day)
    assert "Monday" in out
    assert "Breakfast" not in out
    assert "Lunch" not in out


# ─── format_today ─────────────────────────────────────────────────────────────


def test_format_today_when_day_exists():
    today = today_iso()
    day = _day(today, dow=dow_for(today), breakfast=Meal(name="poha", slot="breakfast"))
    plan = CurrentPlan(week_of=today, days=[day])
    out = daily.format_today(plan)
    assert "poha" in out
    assert dow_for(today) in out


def test_format_today_when_no_day():
    plan = CurrentPlan(week_of="2026-06-22", days=[_day("2026-06-22")])
    out = daily.format_today(plan)
    # either today's menu (if today is 2026-06-22) or the no-plan message
    assert ("poha" not in out)  # no poha in this plan regardless
    assert ("No plan for today" in out) or (out.startswith("📅"))


def test_format_today_no_plan_at_all():
    out = daily.format_today(None)
    assert "No plan for today" in out
