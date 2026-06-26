"""
Builds a YouTubeService for a given Channel, transparently refreshing
the access token if it's expired and persisting the new token back to
the DB. Centralizing this means no caller (API route or Celery task)
has to remember to handle token refresh itself.
"""
from datetime import datetime, timezone

from google.auth.transport.requests import Request as GoogleAuthRequest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Channel
from app.services.token_cipher import TokenCipher
from app.services.youtube_service import YouTubeService


async def get_youtube_service_for_channel(channel: Channel, db: AsyncSession) -> YouTubeService:
    cipher = TokenCipher()
    access_token = cipher.decrypt(channel.access_token_encrypted)
    refresh_token = cipher.decrypt(channel.refresh_token_encrypted)

    yt = YouTubeService(access_token=access_token, refresh_token=refresh_token)

    is_expired = channel.token_expiry is not None and channel.token_expiry <= datetime.now(timezone.utc)
    if is_expired or yt.credentials.expired:
        yt.credentials.refresh(GoogleAuthRequest())

        # Persist the newly refreshed access token (refresh_token stays
        # the same unless Google rotates it, which credentials object
        # would reflect automatically).
        channel.access_token_encrypted = cipher.encrypt(yt.credentials.token)
        if yt.credentials.refresh_token:
            channel.refresh_token_encrypted = cipher.encrypt(yt.credentials.refresh_token)
        channel.token_expiry = yt.credentials.expiry
        await db.commit()

    return yt
