"""Pure game rules: XP, levels, unlocks, streaks, stats. No database access here."""

from dataclasses import dataclass
from datetime import date, datetime

BASE_XP = {"main": 200, "side": 50, "daily": 30, "timed": 50, "home": 15}
PROOF_BONUS = 10
NEW_CELL_BONUS = 20
TIMED_BONUS_MULTIPLIER = 1.5
CASH_PER_MISSION = {"main": 40, "side": 10, "daily": 5, "timed": 10, "home": 2}

# Level → unlocks (mirrors the "Levels and unlocks" table in the design doc).
# feature_key, value, label
DEFAULT_UNLOCKS: list[tuple[int, str, str, str]] = [
    (1, "slots", "3", "3 active mission slots"),
    (1, "mission_type:side", "true", "Side missions"),
    (1, "mission_type:home", "true", "Home missions"),
    (1, "mission_type:timed", "true", "Timed missions"),
    (3, "mission_type:main", "true", "Main story missions"),
    (3, "slots", "5", "5 active mission slots"),
    (5, "daily_events", "true", "Daily random events"),
    (8, "send_to_friends", "true", "Send missions to friends"),
    (10, "squads", "true", "Squads and co-op missions"),
    (10, "slots", "8", "8 active mission slots"),
    (15, "map_skins", "true", "Map skins (night city, retro, neon)"),
    (20, "public_missions", "true", "Create public missions others can take"),
]

PREMIUM_EXTRA_SLOTS = 2
MAP_SKINS = ("default", "night", "retro", "neon")

CATEGORY_TO_STAT = {"fitness": "stat_stamina", "explore": "stat_explorer", "social": "stat_social"}


def xp_to_next(level: int) -> int:
    """XP needed to go from `level` to `level + 1` = 100 × L^1.5 (rounded)."""
    return round(100 * level**1.5)


def level_from_xp(total_xp: int) -> int:
    level, remaining = 1, total_xp
    while remaining >= xp_to_next(level):
        remaining -= xp_to_next(level)
        level += 1
    return level


def level_progress(total_xp: int) -> dict:
    level, remaining = 1, total_xp
    while remaining >= xp_to_next(level):
        remaining -= xp_to_next(level)
        level += 1
    return {"level": level, "xp_into_level": remaining, "xp_for_next": xp_to_next(level)}


def unlocks_for_level(level: int, unlocks=DEFAULT_UNLOCKS) -> dict[str, str]:
    """Feature map for a level; later rows override earlier ones (e.g. slots 3 → 5 → 8)."""
    features: dict[str, str] = {}
    for lvl, key, value, _label in sorted(unlocks, key=lambda u: u[0]):
        if lvl <= level:
            features[key] = value
    return features


def new_unlocks_between(old_level: int, new_level: int, unlocks=DEFAULT_UNLOCKS) -> list[dict]:
    return [
        {"level": lvl, "feature_key": key, "value": value, "label": label}
        for lvl, key, value, label in unlocks
        if old_level < lvl <= new_level
    ]


def slot_limit(level: int, is_premium: bool = False) -> int:
    base = int(unlocks_for_level(level).get("slots", "3"))
    return base + (PREMIUM_EXTRA_SLOTS if is_premium else 0)


def can_use_type(level: int, mission_type: str) -> bool:
    if mission_type == "daily":
        return True  # daily missions are created by the system, not the player
    return unlocks_for_level(level).get(f"mission_type:{mission_type}") == "true"


def streak_multiplier(streak_days: int) -> float:
    if streak_days >= 30:
        return 1.5
    if streak_days >= 7:
        return 1.25
    if streak_days >= 3:
        return 1.1
    return 1.0


def next_streak(current: int, last_completion: date | None, today: date) -> int:
    """Streak after completing a mission today."""
    if last_completion == today:
        return max(current, 1)
    if last_completion is not None and (today - last_completion).days == 1:
        return current + 1
    return 1


def has_deadline_bonus(mission_type: str, deadline: datetime | None, now: datetime) -> bool:
    return deadline is not None and now <= deadline


@dataclass
class XpBreakdown:
    base: int
    multiplier: float
    timed_bonus: bool
    proof_bonus: int
    new_cell_bonus: int
    total: int


def calculate_xp(
    mission_type: str,
    streak_days: int,
    in_time: bool,
    proof_attached: bool,
    first_visit_to_cell: bool,
) -> XpBreakdown:
    """xp = base × streak_multiplier × (1.5 if timed and before deadline) + 10 proof + 20 new cell."""
    base = BASE_XP.get(mission_type, 50)
    mult = streak_multiplier(streak_days)
    timed = 1.5 if in_time else 1.0
    proof = PROOF_BONUS if proof_attached else 0
    cell = NEW_CELL_BONUS if first_visit_to_cell else 0
    total = round(base * mult * timed) + proof + cell
    return XpBreakdown(base, mult, in_time, proof, cell, total)


def slacker_stars_after_day(current_stars: int, had_active_missions: bool, completed_today: bool) -> int:
    """Wanted level in reverse: +1 star per idle day with active missions (max 5); any completion clears."""
    if completed_today:
        return 0
    if had_active_missions:
        return min(5, current_stars + 1)
    return current_stars
