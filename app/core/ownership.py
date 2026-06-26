"""
Ownership-check dependencies. A logged-in user should only ever be able
to read/modify channels and videos they own — these helpers enforce
that at the route level so individual endpoints don't have to remember to.
"""
import uuid

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.user import User, Channel
from app.models.video import Video


async def get_owned_channel(
    channel_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Channel:
    channel = await db.get(Channel, channel_id)
    if not channel or channel.owner_id != user.id:
        # 404, not 403 — don't reveal whether a channel_id exists for a
        # different user.
        raise HTTPException(404, "Channel not found.")
    return channel


async def get_owned_video(
    video_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Video:
    video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(404, "Video not found.")
    channel = await db.get(Channel, video.channel_id)
    if not channel or channel.owner_id != user.id:
        raise HTTPException(404, "Video not found.")
    return video
