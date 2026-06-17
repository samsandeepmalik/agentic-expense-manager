"""Profiles API: list, create, activate, delete."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..db import get_db
from ..services import audit
from ..services import profiles as svc

router = APIRouter()


class ProfileIn(BaseModel):
    name: str
    kind: str = Field(default="personal", pattern="^(personal|incorporation|other)$")


class PatchProfileIn(BaseModel):
    prompt_loan: bool


@router.get("/api/profiles")
async def list_profiles():
    with get_db() as conn:
        return svc.list_profiles(conn)


@router.post("/api/profiles")
async def create_profile(body: ProfileIn):
    with get_db() as conn:
        profile = svc.create_profile(conn, body.name, body.kind)
        audit.record(conn, "profile_created", channel="ui",
                     ref=str(profile["id"]), detail=f"{body.name} ({body.kind})")
        return profile


@router.post("/api/profiles/{profile_id}/activate")
async def activate(profile_id: int):
    with get_db() as conn:
        profile = svc.set_active(conn, profile_id)
        audit.record(conn, "profile_activated", channel="ui",
                     ref=str(profile_id), detail=profile["name"])
        return profile


@router.patch("/api/profiles/{profile_id}")
async def patch_profile(profile_id: int, body: PatchProfileIn):
    with get_db() as conn:
        profile = svc.update_profile(conn, profile_id, body.prompt_loan)
        audit.record(conn, "profile_updated", channel="ui",
                     ref=str(profile_id),
                     detail=f"prompt_loan={body.prompt_loan}")
        return profile


@router.delete("/api/profiles/{profile_id}")
async def delete_profile(profile_id: int):
    with get_db() as conn:
        svc.delete_profile(conn, profile_id)
        audit.record(conn, "profile_deleted", channel="ui", ref=str(profile_id))
    return {"ok": True}
