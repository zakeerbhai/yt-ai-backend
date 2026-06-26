"""
API request/response schemas (separate from DB models so we control
exactly what's exposed publicly — e.g. we never serialize OAuth tokens).
"""
import uuid
from datetime import datetime
from pydantic import BaseModel, Field

from app.models.video import VideoStatus


class VideoUploadResponse(BaseModel):
    id: uuid.UUID
    status: VideoStatus
    original_filename: str
    message: str = "Video received. Processing pipeline started."

    model_config = {"from_attributes": True}


class GeneratedContentOut(BaseModel):
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    hashtags: list[str] | None = None
    pinned_comment: str | None = None
    community_post: str | None = None
    thumbnail_text: str | None = None
    thumbnail_suggestions: list[str] | None = None


class VideoDetailOut(BaseModel):
    id: uuid.UUID
    channel_id: uuid.UUID
    status: VideoStatus
    error_message: str | None = None

    original_filename: str
    cloudinary_video_url: str | None = None
    duration_seconds: float | None = None

    transcript_text: str | None = None
    transcript_confidence: float | None = None

    ai_title: str | None = None
    ai_description: str | None = None
    ai_tags: list[str] | None = None
    ai_hashtags: list[str] | None = None
    ai_pinned_comment: str | None = None
    ai_community_post: str | None = None
    ai_thumbnail_text: str | None = None
    ai_thumbnail_suggestions: list[str] | None = None

    final_title: str | None = None
    final_description: str | None = None
    final_tags: list[str] | None = None
    final_thumbnail_url: str | None = None

    suggested_publish_at: datetime | None = None
    scheduled_publish_at: datetime | None = None
    published_at: datetime | None = None
    youtube_video_id: str | None = None

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class VideoApproveRequest(BaseModel):
    """
    Human review step: the creator can accept AI content as-is, or
    override any field, before it's scheduled/published.
    """
    final_title: str | None = Field(None, max_length=100)
    final_description: str | None = None
    final_tags: list[str] | None = None
    final_thumbnail_url: str | None = None
    playlist_ids: list[str] | None = None
    scheduled_publish_at: datetime | None = None  # if None, use AI-suggested time


class VideoScheduleRequest(BaseModel):
    scheduled_publish_at: datetime
