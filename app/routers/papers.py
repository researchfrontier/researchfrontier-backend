from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Topic, Work
from ..schemas import PaperOut
from ..services.serialize import work_to_paper

router = APIRouter(prefix="/api/papers", tags=["papers"])


@router.get("/{work_id}", response_model=PaperOut)
async def paper_detail(
    work_id: int, session: AsyncSession = Depends(get_session)
) -> PaperOut:
    row = (
        await session.execute(
            select(Work, Topic.display_name)
            .join(Topic, Work.primary_topic_id == Topic.id, isouter=True)
            .where(Work.id == work_id)
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="work not found")
    work, topic_name = row
    return work_to_paper(work, topic_name)
