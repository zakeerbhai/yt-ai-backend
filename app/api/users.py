"""
Current-user and channel-management routes.
"""
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.ownership import get_owned_channel
from app.models.user import User, Channel, AutoPublishMode

router = APIRouter(prefix="/api", tags=["users"])


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    avatar_url: str | None

    model_config = {"from_attributes": True}


class ChannelOut(BaseModel):
    id: uuid.UUID
    youtube_channel_id: str
    title: str
    thumbnail_url: str | None
    auto_publish_mode: AutoPublishMode
    is_active: bool

    model_config = {"from_attributes": True}


class ChannelSettingsUpdate(BaseModel):
    auto_publish_mode: AutoPublishMode | None = None
    publish_rules: dict | None = None


@router.get("/me", response_model=UserOut)
async def get_me(user: User = Depends(get_current_user)):
    return UserOut.model_validate(user)


@router.get("/channels", response_model=list[ChannelOut])
async def list_my_channels(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Channel).where(Channel.owner_id == user.id, Channel.is_active == True)  # noqa: E712
    )
    return [ChannelOut.model_validate(c) for c in result.scalars().all()]


@router.patch("/channels/{channel_id}", response_model=ChannelOut)
async def update_channel_settings(
    payload: ChannelSettingsUpdate,
    channel: Channel = Depends(get_owned_channel),
    db: AsyncSession = Depends(get_db),
):
    """
    Lets a user set per-channel automation rules — e.g. switching a
    channel to `full_auto` once they trust the pipeline's output for it.
    """
    if payload.auto_publish_mode is not None:
        channel.auto_publish_mode = payload.auto_publish_mode
    if payload.publish_rules is not None:
        channel.publish_rules = payload.publish_rules
    await db.commit()
    await db.refresh(channel)
    return ChannelOut.model_validate(channel)
