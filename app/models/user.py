"""
User and YouTube Channel models.

A User authenticates via Firebase. A User can connect multiple YouTube
Channels (multi-channel support) via OAuth, each with its own tokens and
auto-publish rules.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, ForeignKey, Enum, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    firebase_uid: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    channels: Mapped[list["Channel"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    fcm_tokens: Mapped[list["DeviceToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class AutoPublishMode(str, enum.Enum):
    MANUAL_REVIEW = "manual_review"        # AI generates content, human must approve before publish
    AUTO_SCHEDULE = "auto_schedule"        # AI picks best time, schedules, but waits for approval to go live
    FULL_AUTO = "full_auto"                # AI generates + schedules + publishes with zero human step


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    youtube_channel_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # OAuth tokens — stored encrypted at rest in production (see services/crypto.py).
    # Never logged, never returned in API responses.
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    auto_publish_mode: Mapped[AutoPublishMode] = mapped_column(
        Enum(AutoPublishMode), default=AutoPublishMode.MANUAL_REVIEW, nullable=False
    )
    # Per-channel rule config, e.g. {"min_confidence": 0.8, "blackout_hours": [0,1,2]}
    publish_rules: Mapped[dict] = mapped_column(JSON, default=dict)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    owner: Mapped["User"] = relationship(back_populates="channels")
    videos: Mapped[list["Video"]] = relationship(back_populates="channel", cascade="all, delete-orphan")


class DeviceToken(Base):
    """Firebase Cloud Messaging device token for push notifications."""
    __tablename__ = "device_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    fcm_token: Mapped[str] = mapped_column(String(512), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), default="web")  # web | ios | android
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="fcm_tokens")
