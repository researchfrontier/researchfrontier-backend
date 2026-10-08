from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Domain, Field, Subfield
from ..schemas import Breadcrumb, DomainNode, FieldNode, SubfieldNode
from ..services.serialize import get_breadcrumb

router = APIRouter(prefix="/api/taxonomy", tags=["taxonomy"])


@router.get("/tree", response_model=list[DomainNode])
async def tree(session: AsyncSession = Depends(get_session)) -> list[DomainNode]:
    """The browsable research-field taxonomy: domains → fields → subfields."""
    domains = (await session.scalars(select(Domain).order_by(Domain.display_name))).all()
    fields = (await session.scalars(select(Field).order_by(Field.display_name))).all()
    subfields = (await session.scalars(select(Subfield).order_by(Subfield.display_name))).all()

    subs_by_field: dict[int, list[SubfieldNode]] = defaultdict(list)
    for s in subfields:
        subs_by_field[s.field_id].append(
            SubfieldNode(id=s.id, name=s.display_name, works_count=s.works_count)
        )

    fields_by_domain: dict[int, list[FieldNode]] = defaultdict(list)
    for f in fields:
        fields_by_domain[f.domain_id].append(
            FieldNode(
                id=f.id,
                name=f.display_name,
                works_count=f.works_count,
                subfields=subs_by_field.get(f.id, []),
            )
        )

    return [
        DomainNode(
            id=d.id,
            name=d.display_name,
            works_count=d.works_count,
            fields=fields_by_domain.get(d.id, []),
        )
        for d in domains
    ]


@router.get("/subfields/{subfield_id}", response_model=Breadcrumb)
async def subfield_breadcrumb(
    subfield_id: int, session: AsyncSession = Depends(get_session)
) -> Breadcrumb:
    return await get_breadcrumb(session, subfield_id)
