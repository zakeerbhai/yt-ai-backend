"""
Video API routes: upload, status polling, review/approve, scheduling.

Every route requires a verified Firebase user (`get_current_user`) and
verifies that user owns the channel/video being acted on
(`get_owned_channel` / `get_owned_video`) — see app/core/ownership.py.
"""
import os
import tempfile
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.ownership import get_owned_channel, get_owned_video
from app.models.user import User, Channel
from app.models.video import Video, VideoStatus
from app.schemas.video import (
    VideoUploadResponse,
    VideoDetailOut,
    VideoApproveRequest,
    VideoScheduleRequest,
)
from app.workers.video_pipeline import process_video_pipeline

router = APIRouter(prefix="/api/videos", tags=["videos"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB
ALLOWED_CONTENT_TYPES = {"video/mp4", "video/quicktime", "video/x-matroska", "video/webm", "video/x-msvideo"}


@router.post("/upload", response_model=VideoUploadResponse, status_code=201)
async def upload_video(
    channel_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Ownership check done manually here (rather than via the
    # get_owned_channel path dependency) since channel_id arrives as a
    # form field on this particular route, not a path parameter.
    channel = await db.get(Channel, channel_id)
    if not channel or channel.owner_id != user.id:
        raise HTTPException(404, "Channel not found. Connect a YouTube channel first.")
    if not channel.is_active:
        raise HTTPException(409, "This channel is disconnected. Reconnect it before uploading.")

    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(400, f"Unsupported file type: {file.content_type}")

    video = Video(
        channel_id=channel_id,
        original_filename=file.filename,
        status=VideoStatus.UPLOADED,
    )
    db.add(video)
    await db.commit()
    await db.refresh(video)

    # Stream the upload to a temp file on disk (avoid loading large
    # videos fully into memory) — Celery task picks it up from here.
    suffix = os.path.splitext(file.filename)[1] or ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        total_bytes = 0
        while chunk := await file.read(1024 * 1024):
            total_bytes += len(chunk)
            if total_bytes > MAX_UPLOAD_BYTES:
                os.remove(tmp.name)
                raise HTTPException(413, "File too large (max 5GB).")
            tmp.write(chunk)
        local_path = tmp.name

    process_video_pipeline.delay(str(video.id), local_path)

    return VideoUploadResponse.model_validate(video)


@router.get("/{video_id}", response_model=VideoDetailOut)
async def get_video(video: Video = Depends(get_owned_video)):
    return VideoDetailOut.model_validate(video)


@router.get("", response_model=list[VideoDetailOut])
async def list_videos(
    channel: Channel = Depends(get_owned_channel),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Video).where(Video.channel_id == channel.id).order_by(Video.created_at.desc())
    )
    return [VideoDetailOut.model_validate(v) for v in result.scalars().all()]


@router.post("/{video_id}/approve", response_model=VideoDetailOut)
async def approve_video(
    payload: VideoApproveRequest,
    video: Video = Depends(get_owned_video),
    db: AsyncSession = Depends(get_db),
):
    """
    Human review step. Lets the creator accept or override AI-generated
    content, then either schedules or triggers immediate publish
    depending on what the channel's rules say.
    """
    if video.status != VideoStatus.READY_FOR_REVIEW:
        raise HTTPException(409, f"Video is in status '{video.status.value}', not ready for review.")

    if payload.final_title is not None:
        video.final_title = payload.final_title
    if payload.final_description is not None:
        video.final_description = payload.final_description
    if payload.final_tags is not None:
        video.final_tags = payload.final_tags
    if payload.final_thumbnail_url is not None:
        video.final_thumbnail_url = payload.final_thumbnail_url
    if payload.playlist_ids is not None:
        video.playlist_ids = payload.playlist_ids

    video.scheduled_publish_at = payload.scheduled_publish_at or video.suggested_publish_at
    video.status = VideoStatus.SCHEDULED
    await db.commit()
    await db.refresh(video)

    if video.scheduled_publish_at is None:
        from app.workers.publish_pipeline import publish_video_task
        publish_video_task.delay(str(video.id))

    return VideoDetailOut.model_validate(video)


@router.post("/{video_id}/schedule", response_model=VideoDetailOut)
async def schedule_video(
    payload: VideoScheduleRequest,
    video: Video = Depends(get_owned_video),
    db: AsyncSession = Depends(get_db),
):
    if video.status not in (VideoStatus.READY_FOR_REVIEW, VideoStatus.SCHEDULED):
        raise HTTPException(409, f"Cannot schedule a video in status '{video.status.value}'.")

    video.scheduled_publish_at = payload.scheduled_publish_at
    video.status = VideoStatus.SCHEDULED
    await db.commit()
    await db.refresh(video)
    return VideoDetailOut.model_validate(video)
