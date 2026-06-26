"""
AssemblyAI integration: converts video/audio to a text transcript,
including speaker-aware timing data we can later use for caption (SRT/VTT)
generation.
"""
import assemblyai as aai
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_not_exception_type

from app.core.config import get_settings


class TranscriptionResult:
    def __init__(self, transcript_id: str, text: str, confidence: float | None, words: list[dict]):
        self.transcript_id = transcript_id
        self.text = text
        self.confidence = confidence
        self.words = words  # [{"text": "hello", "start": 120, "end": 340}, ...] (ms)

    @property
    def has_speech(self) -> bool:
        """
        AssemblyAI returns status=completed with empty/near-empty text
        for silent or music-only videos — that's not an error, just
        nothing to transcribe. Callers should check this before handing
        the transcript to Gemini, which can't generate meaningful
        metadata from nothing.
        """
        return bool(self.text and self.text.strip() and len(self.text.strip()) >= 10)


class NoSpeechDetectedError(Exception):
    """Raised when a video completes transcription but contains no usable speech."""


class AssemblyAIService:
    def __init__(self):
        settings = get_settings()
        aai.settings.api_key = settings.assemblyai_api_key

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        # Don't retry on a definitive transcription failure (bad audio,
        # unsupported format) — only on transient issues. RuntimeError
        # here means AssemblyAI itself reported status=error, which a
        # retry won't fix.
        retry=retry_if_not_exception_type(RuntimeError),
    )
    def transcribe(self, media_url: str) -> TranscriptionResult:
        """
        Submits a video/audio URL (e.g. the Cloudinary secure_url) to
        AssemblyAI and blocks until the transcript completes. Retries
        on transient network/API errors (connection issues, 5xx) but
        not on AssemblyAI-reported transcription failures.

        In production this should be called from a background worker
        (Celery task), not the request thread, since transcription can
        take anywhere from several seconds to a few minutes depending on
        video length.
        """
        config = aai.TranscriptionConfig(
            speech_model=aai.SpeechModel.best,
            punctuate=True,
            format_text=True,
        )
        transcript = aai.Transcriber().transcribe(media_url, config=config)

        if transcript.status == aai.TranscriptStatus.error:
            raise RuntimeError(f"AssemblyAI transcription failed: {transcript.error}")

        words = [
            {"text": w.text, "start": w.start, "end": w.end}
            for w in (transcript.words or [])
        ]

        return TranscriptionResult(
            transcript_id=transcript.id,
            text=transcript.text or "",
            confidence=transcript.confidence,
            words=words,
        )

    def generate_srt(self, transcript_id: str) -> str:
        """Fetches SRT-formatted captions for a completed transcript."""
        transcript = aai.Transcript.get_by_id(transcript_id)
        return transcript.export_subtitles_srt()

    def generate_vtt(self, transcript_id: str) -> str:
        """Fetches WebVTT-formatted captions for a completed transcript."""
        transcript = aai.Transcript.get_by_id(transcript_id)
        return transcript.export_subtitles_vtt()
