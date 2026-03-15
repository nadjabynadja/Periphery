"""Persistence layer for personal ontology — pins, hides, groups, views."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from periphery.db import get_pool
from periphery.auth.models import (
    EntityAnnotation,
    EntityGroup,
    PersonalOverlay,
    SavedView,
)
from periphery.auth.utils import generate_challenge_id  # reuse for IDs

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_id() -> str:
    return generate_challenge_id()


# ---------------------------------------------------------------------------
# Entity Annotations (pin, hide, tag, note)
# ---------------------------------------------------------------------------

async def set_annotation(
    user_id: str,
    canonical_id: str,
    annotation_type: str,
    annotation_data: dict | None = None,
) -> None:
    pool = get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """INSERT INTO user_entity_annotations
               (user_id, canonical_id, annotation_type, annotation_data, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, canonical_id, annotation_type) DO UPDATE
               SET annotation_data = excluded.annotation_data""",
            (user_id, canonical_id, annotation_type,
             json.dumps(annotation_data or {}), _now()),
        )
        await db.commit()


async def remove_annotation(
    user_id: str,
    canonical_id: str,
    annotation_type: str,
) -> None:
    pool = get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """DELETE FROM user_entity_annotations
               WHERE user_id = ? AND canonical_id = ? AND annotation_type = ?""",
            (user_id, canonical_id, annotation_type),
        )
        await db.commit()


async def get_annotations(user_id: str) -> list[EntityAnnotation]:
    pool = get_pool()
    async with pool.acquire() as db:
        cursor = await db.execute(
            """SELECT canonical_id, annotation_type, annotation_data
               FROM user_entity_annotations WHERE user_id = ?""",
            (user_id,),
        )
        rows = await cursor.fetchall()
    return [
        EntityAnnotation(
            canonical_id=r["canonical_id"],
            annotation_type=r["annotation_type"],
            annotation_data=json.loads(r["annotation_data"]) if r["annotation_data"] else {},
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Entity Groups
# ---------------------------------------------------------------------------

async def create_group(
    user_id: str,
    name: str,
    description: str | None = None,
    entity_ids: list[str] | None = None,
) -> EntityGroup:
    group = EntityGroup(
        group_id=_gen_id(),
        name=name,
        description=description,
        entity_ids=entity_ids or [],
        created_at=datetime.now(timezone.utc),
    )
    pool = get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """INSERT INTO user_entity_groups
               (group_id, user_id, name, description, entity_ids, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (group.group_id, user_id, group.name, group.description,
             json.dumps(group.entity_ids), _now()),
        )
        await db.commit()
    return group


async def update_group(
    user_id: str,
    group_id: str,
    name: str | None = None,
    description: str | None = None,
    entity_ids: list[str] | None = None,
) -> EntityGroup | None:
    pool = get_pool()
    async with pool.acquire() as db:
        # Verify ownership
        cursor = await db.execute(
            "SELECT group_id, name, description, entity_ids, created_at FROM user_entity_groups WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        )
        row = await cursor.fetchone()
        if not row:
            return None

        new_name = name if name is not None else row["name"]
        new_desc = description if description is not None else row["description"]
        new_ids = entity_ids if entity_ids is not None else json.loads(row["entity_ids"] or "[]")

        await db.execute(
            "UPDATE user_entity_groups SET name = ?, description = ?, entity_ids = ? WHERE group_id = ?",
            (new_name, new_desc, json.dumps(new_ids), group_id),
        )
        await db.commit()

    return EntityGroup(
        group_id=group_id,
        name=new_name,
        description=new_desc,
        entity_ids=new_ids,
        created_at=row["created_at"],
    )


async def delete_group(user_id: str, group_id: str) -> bool:
    pool = get_pool()
    async with pool.acquire() as db:
        cursor = await db.execute(
            "DELETE FROM user_entity_groups WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        )
        await db.commit()
    return cursor.rowcount > 0


async def list_groups(user_id: str) -> list[EntityGroup]:
    pool = get_pool()
    async with pool.acquire() as db:
        cursor = await db.execute(
            """SELECT group_id, name, description, entity_ids, created_at
               FROM user_entity_groups WHERE user_id = ? ORDER BY created_at""",
            (user_id,),
        )
        rows = await cursor.fetchall()
    return [
        EntityGroup(
            group_id=r["group_id"],
            name=r["name"],
            description=r["description"],
            entity_ids=json.loads(r["entity_ids"]) if r["entity_ids"] else [],
            created_at=r["created_at"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Saved Views
# ---------------------------------------------------------------------------

async def create_view(
    user_id: str,
    name: str,
    filters: dict | None = None,
    layout: dict | None = None,
) -> SavedView:
    view = SavedView(
        view_id=_gen_id(),
        name=name,
        filters=filters or {},
        layout=layout or {},
        created_at=datetime.now(timezone.utc),
    )
    pool = get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """INSERT INTO user_saved_views
               (view_id, user_id, name, filters, layout, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (view.view_id, user_id, view.name,
             json.dumps(view.filters), json.dumps(view.layout), _now()),
        )
        await db.commit()
    return view


async def delete_view(user_id: str, view_id: str) -> bool:
    pool = get_pool()
    async with pool.acquire() as db:
        cursor = await db.execute(
            "DELETE FROM user_saved_views WHERE view_id = ? AND user_id = ?",
            (view_id, user_id),
        )
        await db.commit()
    return cursor.rowcount > 0


async def list_views(user_id: str) -> list[SavedView]:
    pool = get_pool()
    async with pool.acquire() as db:
        cursor = await db.execute(
            """SELECT view_id, name, filters, layout, created_at
               FROM user_saved_views WHERE user_id = ? ORDER BY created_at""",
            (user_id,),
        )
        rows = await cursor.fetchall()
    return [
        SavedView(
            view_id=r["view_id"],
            name=r["name"],
            filters=json.loads(r["filters"]) if r["filters"] else {},
            layout=json.loads(r["layout"]) if r["layout"] else {},
            created_at=r["created_at"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Full overlay (used by snapshot endpoint)
# ---------------------------------------------------------------------------

async def get_personal_overlay(user_id: str) -> PersonalOverlay:
    annotations = await get_annotations(user_id)
    groups = await list_groups(user_id)
    views = await list_views(user_id)

    pinned = [a.canonical_id for a in annotations if a.annotation_type == "pin"]
    hidden = [a.canonical_id for a in annotations if a.annotation_type == "hide"]

    # Group non-pin/hide annotations by canonical_id
    entity_anns: dict[str, list[EntityAnnotation]] = {}
    for a in annotations:
        if a.annotation_type not in ("pin", "hide"):
            entity_anns.setdefault(a.canonical_id, []).append(a)

    return PersonalOverlay(
        pinned_entity_ids=pinned,
        hidden_entity_ids=hidden,
        custom_groups=groups,
        entity_annotations=entity_anns,
        saved_views=views,
    )
