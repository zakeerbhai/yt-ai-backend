"""
The core automation pipeline:

  uploaded -> transcribe (AssemblyAI) -> generate metadata (Gemini) ->
  ready_for_review (or auto-scheduled/published, depending on the
  channel's auto_publish_mode)

Each stage updates the Video row's `status` so the frontend can poll
progress. Failures at any stage set status=FAILED with error_message
rather than leaving the row stuck silently.

Resumability: each stage checks whether its output already exists on
the Video row before redoing the (often paid, always slow) work. This
matters because the outer Celery task retries on any unhandled
exception — without resumability, a transient failure during Gemini
generation (stage 3 of 4) would cause a retry that re-uploads to
Cloudinary and re-transcribes with AssemblyAI from scratch, burning
time and API quota for work that already succeeded.
"""
import asyncio
import os
import shutil
import tempfile

from app.core.database import AsyncSessionLocal
from app.models.user import AutoPublishMode, Channel
from app.models.video import Video, VideoStatus
from app.services.assemblyai_service import AssemblyAIService, NoSpeechDetectedError
from app.services.gemini_service import GeminiService
from app.services.cloudinary_service import CloudinaryService
from app.workers.celery_app import celery_app


async def _update_video(video_id: str, **fields):
    async with AsyncSessionLocal() as db:
        video = await db.get(Video, video_id)
        if not video:
            return
        for key, value in fields.items():
            setattr(video, key, value)
        await db.commit()


async def _get_video_and_channel(video_id: str) -> tuple[Video | None, Channel | None]:
    async with AsyncSessionLocal() as db:
        video = await db.get(Video, video_id)
        if not video:
            return None, None
        channel = await db.get(Channel, video.channel_id)
        return video, channel


@celery_app.task(name="process_video_pipeline", bind=True, max_retries=2, default_retry_delay=30)
def process_video_pipeline(self, video_id: str, local_video_path: str):
    """
    Entry point invoked right after upload. Runs the full pipeline
    synchronously within the worker (Celery workers are processes, so
    this doesn't block the API). Uses asyncio.run to bridge into our
    async DB/service layer.

    The whole attempt — including failure handling — runs inside a
    single asyncio.run() call, for the same reason documented in
    publish_pipeline.py's task: a second, separate asyncio.run() for
    the except-path would crash trying to reuse a DB connection pool
    bound to the first (now-closed) event loop. Found by actually
    running a worker and watching a failure path execute.

    `_attempt()` records FAILED status for every error type itself
    (so the DB write always happens inside the one live event loop),
    then reports back via the returned outcome string what the outer
    sync function should do — re-raise-and-retry, re-raise-without-retry,
    or do nothing further — rather than relying on exceptions crossing
    the asyncio.run() boundary differently for different error types.
    """

    async def _attempt() -> tuple[str, Exception | None]:
        try:
            await _run_pipeline(video_id, local_video_path)
            return "success", None
        except NoSpeechDetectedError as exc:
            # Not worth retrying — the video genuinely has no speech to
            # transcribe, retrying won't change that outcome.
            await _update_video(video_id, status=VideoStatus.FAILED, error_message=str(exc))
            return "no_retry", exc
        except Exception as exc:
            await _update_video(video_id, status=VideoStatus.FAILED, error_message=str(exc))
            # Only retry if local_video_path still exists — if an
            # earlier attempt already consumed/deleted it (see the
            # finally block in _run_pipeline), a retry would just fail
            # immediately trying to read a missing file, wasting a
            # retry slot for no benefit.
            if os.path.exists(local_video_path):
                return "retry", exc
            return "no_retry", exc

    outcome, exc = asyncio.run(_attempt())
    if outcome == "retry":
        raise self.retry(exc=exc)
    if outcome == "no_retry":
        raise exc


async def _run_pipeline(video_id: str, local_video_path: str):
    video, channel = await _get_video_and_channel(video_id)
    if not video or not channel:
        raise RuntimeError(f"Video {video_id} or its channel not found.")

    tmp_dir = tempfile.mkdtemp(prefix="yt_ai_")
    file_consumed = False
    try:
        cloudinary_svc = CloudinaryService()

        # --- Stage 1: Cloudinary upload (storage + delivery URL) ---
        # Resumable: if a previous attempt already produced a Cloudinary
        # URL for this video, skip re-uploading.
        if video.cloudinary_video_url:
            upload_result = {
                "public_id": video.cloudinary_public_id,
                "secure_url": video.cloudinary_video_url,
                "duration": video.duration_seconds,
            }
        else:
            upload_result = cloudinary_svc.upload_video(local_video_path, public_id=f"video_{video_id}")
            await _update_video(
                video_id,
                cloudinary_public_id=upload_result["public_id"],
                cloudinary_video_url=upload_result["secure_url"],
                duration_seconds=upload_result.get("duration"),
                status=VideoStatus.TRANSCRIBING,
            )

        # The local upload is only needed for the Cloudinary step above.
        # Once that's succeeded (this attempt or a prior one), we're
        # done with the local file regardless of what happens next.
        file_consumed = True

        # --- Stage 2: Transcribe with AssemblyAI ---
        # AssemblyAI accepts a remote media URL directly and extracts
        # audio itself server-side — no local audio extraction step is
        # needed before this call. Resumable: skip if a transcript was
        # already saved by a prior attempt.
        if video.transcript_text:
            transcript_text = video.transcript_text
            transcript_confidence = video.transcript_confidence
        else:
            assembly_svc = AssemblyAIService()
            transcription = assembly_svc.transcribe(upload_result["secure_url"])

            if not transcription.has_speech:
                raise NoSpeechDetectedError(
                    "No usable speech was found in this video (it may be silent, music-only, "
                    "or too short to transcribe). Title/description can't be generated "
                    "automatically — you can still publish it manually with your own metadata."
                )

            transcript_text = transcription.text
            transcript_confidence = transcription.confidence
            await _update_video(
                video_id,
                assemblyai_transcript_id=transcription.transcript_id,
                transcript_text=transcript_text,
                transcript_confidence=transcript_confidence,
                status=VideoStatus.TRANSCRIBED,
            )

        # --- Stage 3: Generate metadata with Gemini ---
        # Resumable: skip if AI content was already generated by a
        # prior attempt (status would already be past this point, but
        # we also check the field directly in case of a partial write).
        if not video.ai_title:
            await _update_video(video_id, status=VideoStatus.GENERATING_CONTENT)
            gemini_svc = GeminiService()
            content = gemini_svc.generate_metadata(transcript_text)

            # --- Stage 4: Suggest a thumbnail frame from the video itself ---
            thumb_url = cloudinary_svc.generate_thumbnail_from_video(
                upload_result["public_id"], second=min(2.0, (upload_result.get("duration") or 4) / 2)
            )
            thumbnail_suggestions = [thumb_url] if thumb_url else []

            await _update_video(
                video_id,
                ai_title=content.title,
                ai_description=content.description,
                ai_tags=content.tags,
                ai_hashtags=content.hashtags,
                ai_pinned_comment=content.pinned_comment,
                ai_community_post=content.community_post,
                ai_thumbnail_text=content.thumbnail_text,
                ai_thumbnail_suggestions=thumbnail_suggestions,
                ai_generation_raw=content.model_dump(),
                # Pre-fill "final" fields with AI output so review UI has
                # something sane to show/edit even before a human touches it.
                final_title=content.title,
                final_description=content.description,
                final_tags=content.tags,
                final_thumbnail_url=thumb_url,
                status=VideoStatus.READY_FOR_REVIEW,
            )

        # --- Stage 5: Apply channel auto-publish rules ---
        if channel.auto_publish_mode == AutoPublishMode.FULL_AUTO:
            from app.workers.publish_pipeline import publish_video_task
            publish_video_task.delay(str(video_id))
        # MANUAL_REVIEW and AUTO_SCHEDULE both stop here and wait for a
        # human action via the API (approve/schedule endpoints).

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        # Only delete the local upload once it's actually been consumed
        # (uploaded to Cloudinary). If we failed before that point, a
        # retry will need the file to still be there.
        if file_consumed and os.path.exists(local_video_path):
            os.remove(local_video_path)
