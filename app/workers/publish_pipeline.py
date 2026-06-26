"""
Publishes an approved/scheduled video to YouTube via the Data API.
Separate from the generation pipeline so it can be triggered either
immediately (FULL_AUTO mode) or later by the scheduler.

Retry safety: the YouTube upload itself is the one step that must
NEVER run twice for the same video — a retry that re-uploads would
publish a duplicate to the creator's real channel. So `youtube_video_id`
is persisted to the DB the instant the upload call returns, and is
checked first on every attempt: once it's set, we never call
`upload_video` again for this row, retry or not. Only the steps after
upload (thumbnail, playlist, pinned comment) are safe to retry, since
they're idempotent-ish (re-posting a comment is a minor annoyance, not
a duplicate publish) or cheap to guard individually.
"""
import asyncio
import os
import tempfile
from datetime import datetime, timezone

import httpx

from app.core.database import AsyncSessionLocal
from app.models.user import Channel
from app.models.video import Video, VideoStatus
from app.services.youtube_auth_helper import get_youtube_service_for_channel
from app.workers.celery_app import celery_app


@celery_app.task(name="publish_video", bind=True, max_retries=3, default_retry_delay=60)
def publish_video_task(self, video_id: str):
    """
    Runs the whole attempt — including failure handling — inside a
    single asyncio.run() call. Calling asyncio.run() separately for the
    success path and the except-path each creates its own event loop;
    since SQLAlchemy's AsyncEngine (a module-level singleton) caches a
    connection pool tied to whichever loop created it, the second
    asyncio.run() call would try to reuse a connection bound to the
    now-closed first loop and crash with "attached to a different loop"
    — found by actually running a worker and watching a task fail, not
    by reading the code. A single outer async function sidesteps this
    entirely: one task execution, one event loop, no pool handoff.
    """
    async def _attempt():
        try:
            await _publish(video_id)
        except Exception as exc:
            await _mark_failed(video_id, str(exc))
            raise

    try:
        asyncio.run(_attempt())
    except Exception as exc:
        raise self.retry(exc=exc)


async def _mark_failed(video_id: str, message: str):
    async with AsyncSessionLocal() as db:
        video = await db.get(Video, video_id)
        if video:
            video.status = VideoStatus.FAILED
            video.error_message = f"Publish failed: {message}"
            await db.commit()


async def _publish(video_id: str):
    async with AsyncSessionLocal() as db:
        video = await db.get(Video, video_id)
        if not video:
            raise RuntimeError(f"Video {video_id} not found.")
        channel = await db.get(Channel, video.channel_id)
        if not channel:
            raise RuntimeError(f"Channel for video {video_id} not found.")

        yt = await get_youtube_service_for_channel(channel, db)

        # --- The one-and-only-one-time step: upload to YouTube ---
        # If a prior attempt already got a youtube_video_id, the upload
        # already happened — skip straight to the (safe-to-retry) steps
        # after it, no matter why this attempt was triggered.
        if not video.youtube_video_id:
            video.status = VideoStatus.PUBLISHING
            await db.commit()

            tmp_path = None
            try:
                # Download the Cloudinary-hosted video locally — YouTube's
                # resumable upload API requires a file/stream, not a URL.
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_file:
                    async with httpx.AsyncClient(timeout=600) as client:
                        async with client.stream("GET", video.cloudinary_video_url) as response:
                            response.raise_for_status()
                            async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                                tmp_file.write(chunk)
                    tmp_path = tmp_file.name

                upload_response = yt.upload_video(
                    file_path=tmp_path,
                    title=video.final_title or video.ai_title,
                    description=video.final_description or video.ai_description,
                    tags=video.final_tags or video.ai_tags or [],
                    publish_at=video.scheduled_publish_at,
                )

                # Persist immediately — this is the line that prevents a
                # retry from uploading a duplicate. Everything below this
                # point can fail and retry safely; this can't.
                video.youtube_video_id = upload_response["id"]
                video.published_at = datetime.now(timezone.utc)
                await db.commit()
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)

        youtube_video_id = video.youtube_video_id

        # --- Thumbnail (best-effort; safe to retry, won't duplicate-publish) ---
        if video.final_thumbnail_url:
            tmp_thumb_path = None
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.get(video.final_thumbnail_url)
                    resp.raise_for_status()
                    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp_thumb:
                        tmp_thumb.write(resp.content)
                        tmp_thumb_path = tmp_thumb.name
                yt.set_thumbnail(youtube_video_id, tmp_thumb_path)
            finally:
                if tmp_thumb_path and os.path.exists(tmp_thumb_path):
                    os.remove(tmp_thumb_path)

        # --- Playlists ---
        if video.playlist_ids:
            for playlist_id in video.playlist_ids:
                yt.add_video_to_playlist(playlist_id, youtube_video_id)

        # --- Pinned comment (posted, not actually pinned — see
        # YouTubeService.post_pinned_comment docstring: the Data API has
        # no "pin" endpoint, so this needs manual pinning in Studio) ---
        if video.ai_pinned_comment:
            yt.post_pinned_comment(youtube_video_id, video.ai_pinned_comment)

        video.status = VideoStatus.PUBLISHED
        await db.commit()
