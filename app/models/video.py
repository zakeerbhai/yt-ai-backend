"""
Video model — represents one uploaded video through its entire lifecycle:
upload -> audio extraction -> transcription -> AI content generation ->
(review) -> scheduled -> published, plus generated content + analytics snapshot.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, ForeignKey, Enum, JSON, Float, Integer, BigInteger
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class VideoStatus(str, enum.Enum):
    UPLOADED = "uploaded"                  # raw file stored in Cloudinary
    TRANSCRIBING = "transcribing"          # AssemblyAI job in progress
    TRANSCRIBED = "transcribed"
    GENERATING_CONTENT = "generating_content"  # Gemini job in progress
    READY_FOR_REVIEW = "ready_for_review"  # AI content ready, awaiting human (if mode requires it)
    SCHEDULED = "scheduled"                # queued for publish at a specific time
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channels.id"), nullable=False)

    # --- Source file ---
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    cloudinary_public_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cloudinary_video_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- Pipeline state ---
    status: Mapped[VideoStatus] = mapped_column(Enum(VideoStatus), default=VideoStatus.UPLOADED, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Transcript (AssemblyAI) ---
    assemblyai_transcript_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- AI-generated content (Gemini) ---
    ai_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ai_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_tags: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    ai_hashtags: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    ai_pinned_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_community_post: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_thumbnail_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ai_thumbnail_suggestions: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)  # Cloudinary URLs
    ai_generation_raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # full raw Gemini response for audit

    # --- Human overrides (what actually gets published, if edited after AI generation) ---
    final_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    final_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_tags: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    final_thumbnail_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    playlist_ids: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)

    # --- Scheduling / publishing ---
    suggested_publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    youtube_video_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    channel: Mapped["Channel"] = relationship(back_populates="videos")
    analytics_snapshots: Mapped[list["AnalyticsSnapshot"]] = relationship(
        back_populates="video", cascade="all, delete-orphan"
    )


class AnalyticsSnapshot(Base):
    """
    Point-in-time snapshot of a published video's performance, pulled
    periodically from the YouTube Analytics API.
    """
    __tablename__ = "analytics_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("videos.id"), nullable=False)

    views: Mapped[int] = mapped_column(BigInteger, default=0)
    watch_time_minutes: Mapped[float] = mapped_column(Float, default=0)
    average_view_duration_seconds: Mapped[float] = mapped_column(Float, default=0)
    average_view_percentage: Mapped[float] = mapped_column(Float, default=0)  # retention
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    subscribers_gained: Mapped[int] = mapped_column(Integer, default=0)
    impressions: Mapped[int] = mapped_column(BigInteger, default=0)
    impressions_ctr: Mapped[float] = mapped_column(Float, default=0)

    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    video: Mapped["Video"] = relationship(back_populates="analytics_snapshots")
