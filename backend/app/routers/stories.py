from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.deps import CurrentUser, Db, require_unlock
from app.models import Mission, Story, User
from app.schemas import StoryIn, StoryOut
from app.services.geo import point_wkt
from app.services.missions import ensure_free_slot, ensure_safe_location, to_out

router = APIRouter(prefix="/stories", tags=["stories"])


async def story_out(db, story: Story, user: User) -> StoryOut:
    chapters = (
        await db.execute(select(Mission).where(Mission.story_id == story.id).order_by(Mission.chapter_no))
    ).scalars().all()
    return StoryOut(id=story.id, title=story.title, status=story.status, chapters=[to_out(c, user) for c in chapters])


@router.get("", response_model=list[StoryOut])
async def list_stories(user: CurrentUser, db: Db):
    stories = (
        await db.execute(select(Story).where(Story.owner_id == user.id).order_by(Story.created_at.desc()))
    ).scalars().all()
    return [await story_out(db, s, user) for s in stories]


@router.post("", response_model=StoryOut, status_code=201)
async def create_story(
    body: StoryIn, db: Db, user: User = Depends(require_unlock("mission_type:main"))
):
    """A big goal split into chapters; chapter N+1 unlocks after N. Only the active chapter uses a slot."""
    await ensure_free_slot(db, user)
    for ch in body.chapters:
        if ch.lat is not None and ch.lng is not None:
            await ensure_safe_location(db, ch.lat, ch.lng)
    story = Story(owner_id=user.id, title=body.title)
    db.add(story)
    await db.flush()
    for i, ch in enumerate(body.chapters, start=1):
        db.add(
            Mission(
                owner_id=user.id,
                title=ch.title,
                description=ch.description,
                type="main",
                category=ch.category,
                location=point_wkt(ch.lat, ch.lng) if ch.lat is not None else None,
                place_name=ch.place_name,
                radius_m=ch.radius_m,
                dwell_min=ch.dwell_min,
                proof_required=ch.proof_required,
                story_id=story.id,
                chapter_no=i,
                status="active" if i == 1 else "draft",
                source="self",
            )
        )
    await db.commit()
    return await story_out(db, story, user)


@router.get("/{story_id}", response_model=StoryOut)
async def get_story(story_id: int, user: CurrentUser, db: Db):
    story = await db.get(Story, story_id)
    if story is None or story.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Story not found")
    return await story_out(db, story, user)
