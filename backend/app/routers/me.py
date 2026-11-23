from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, func, select

from app.deps import CurrentUser, Db
from app.models import CheckinAttempt, Completion, ExploredCell, Mission
from app.schemas import MeOut, MeUpdate, StatsOut
from app.services import progression, storage
from app.services.geo import latlng_of
from app.services.missions import active_slots_used

router = APIRouter(prefix="/me", tags=["me"])

SHOP = {
    # item: (price in cash, required level)
    "skin:night": (300, 15),
    "skin:retro": (300, 15),
    "skin:neon": (500, 15),
    "icon:neon": (100, 1),
    "icon:pixel": (100, 1),
    "slot": (400, 1),
}
MAX_EXTRA_SLOTS = 3


async def build_me(db, user) -> MeOut:
    prog = progression.level_progress(user.xp)
    explored = await db.scalar(select(func.count()).select_from(ExploredCell).where(ExploredCell.user_id == user.id))
    completed = await db.scalar(select(func.count()).select_from(Completion).where(Completion.user_id == user.id))
    next_unlocks = [
        {"level": lvl, "feature_key": key, "label": label}
        for lvl, key, _v, label in progression.DEFAULT_UNLOCKS
        if lvl > user.level
    ][:3]
    return MeOut(
        id=user.id,
        username=user.username,
        email=user.email,
        level=user.level,
        xp=user.xp,
        xp_into_level=prog["xp_into_level"],
        xp_for_next=prog["xp_for_next"],
        cash=user.cash,
        streak_days=user.streak_days,
        streak_multiplier=progression.streak_multiplier(user.streak_days),
        slacker_stars=user.slacker_stars,
        travel_mode=user.travel_mode,
        map_skin=user.map_skin,
        is_premium=user.is_premium,
        xp_frozen=user.xp_frozen,
        slots_total=progression.slot_limit(user.level, user.is_premium) + user.extra_slots,
        slots_used=await active_slots_used(db, user.id),
        stats=StatsOut(
            stamina=user.stat_stamina,
            explorer=user.stat_explorer,
            social=user.stat_social,
            discipline=user.stat_discipline,
        ),
        unlocks=progression.unlocks_for_level(user.level),
        next_unlocks=next_unlocks,
        explored_cells=explored or 0,
        completed_missions=completed or 0,
    )


@router.get("", response_model=MeOut)
async def get_me(user: CurrentUser, db: Db):
    return await build_me(db, user)


@router.patch("", response_model=MeOut)
async def update_me(body: MeUpdate, user: CurrentUser, db: Db):
    if body.travel_mode:
        user.travel_mode = body.travel_mode
    if body.map_skin:
        owned = user.owned_skins.split(",")
        if body.map_skin != "default" and not (user.is_premium or body.map_skin in owned):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Buy this skin in the shop first")
        user.map_skin = body.map_skin
    await db.commit()
    return await build_me(db, user)


class BuyIn(BaseModel):
    item: str


@router.post("/shop", response_model=MeOut)
async def buy(body: BuyIn, user: CurrentUser, db: Db):
    """Spend in-game cash on cosmetic map skins, marker icons and extra slots."""
    if body.item not in SHOP:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown item")
    price, level = SHOP[body.item]
    if user.level < level:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Locked — reach level {level}")
    if user.cash < price:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Not enough cash")
    kind, _, name = body.item.partition(":")
    if kind == "skin":
        owned = set(user.owned_skins.split(","))
        if name in owned:
            raise HTTPException(status.HTTP_409_CONFLICT, "Already owned")
        user.owned_skins = ",".join(sorted(owned | {name}))
    elif kind == "icon":
        user.marker_icon = name
    elif kind == "slot":
        if user.extra_slots >= MAX_EXTRA_SLOTS:
            raise HTTPException(status.HTTP_409_CONFLICT, "Max extra slots reached")
        user.extra_slots += 1
    user.cash -= price
    await db.commit()
    return await build_me(db, user)


@router.get("/shop")
async def shop(user: CurrentUser):
    return [
        {"item": k, "price": p, "level": lvl, "owned": k.startswith("skin:") and k[5:] in user.owned_skins.split(",")}
        for k, (p, lvl) in SHOP.items()
    ]


@router.get("/history")
async def history(user: CurrentUser, db: Db, limit: int = 50):
    rows = (
        await db.execute(
            select(Completion, Mission.title, Mission.type)
            .join(Mission, Mission.id == Completion.mission_id)
            .where(Completion.user_id == user.id)
            .order_by(Completion.completed_at.desc())
            .limit(min(limit, 500))
        )
    ).all()
    return [
        {
            "mission_id": c.mission_id,
            "title": title,
            "type": mtype,
            "xp_awarded": c.xp_awarded,
            "completed_at": c.completed_at,
            "proof_url": storage.signed_url(c.proof_url),
        }
        for c, title, mtype in rows
    ]


@router.get("/heatmap")
async def heatmap(user: CurrentUser, db: Db):
    """Personal heatmap: completed mission points as GeoJSON."""
    rows = (
        await db.execute(
            select(Completion.location, Completion.completed_at).where(
                Completion.user_id == user.id, Completion.location.is_not(None)
            )
        )
    ).all()
    features = []
    for loc, at in rows:
        lat, lng = latlng_of(loc)
        features.append(
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lng, lat]}, "properties": {"at": at.isoformat()}}
        )
    return {"type": "FeatureCollection", "features": features}


@router.delete("", status_code=204)
async def delete_account(user: CurrentUser, db: Db):
    """Account deletion removes all location rows (DPDP Act 2023)."""
    await db.execute(delete(CheckinAttempt).where(CheckinAttempt.user_id == user.id))
    await db.execute(delete(ExploredCell).where(ExploredCell.user_id == user.id))
    await db.execute(delete(Completion).where(Completion.user_id == user.id))
    await db.delete(user)  # cascades missions, stories, friendships, squad memberships
    await db.commit()
    storage.delete_user_files(user.id)
