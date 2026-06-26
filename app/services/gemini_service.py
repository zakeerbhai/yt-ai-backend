"""
Gemini integration: takes a video transcript and generates all SEO/content
metadata in a single structured call (title, description, tags, hashtags,
pinned comment, community post, thumbnail text).

We force JSON output via response_mime_type + a strict schema-shaped prompt,
then validate with Pydantic so a malformed model response fails loudly
instead of silently producing garbage that gets published.
"""
import json
import google.generativeai as genai
from pydantic import BaseModel, Field, ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import get_settings


class GeneratedContent(BaseModel):
    title: str = Field(..., max_length=100, description="SEO-optimized YouTube title")
    description: str = Field(..., description="Full YouTube description with keywords, CTA, timestamps if relevant")
    tags: list[str] = Field(..., description="YouTube video tags (not hashtags)")
    hashtags: list[str] = Field(..., description="Hashtags including the leading #")
    pinned_comment: str = Field(..., description="A short engaging comment the creator can pin")
    community_post: str = Field(..., description="A short community-tab post promoting this video")
    thumbnail_text: str = Field(..., max_length=40, description="Short punchy text overlay for the thumbnail")
    content_summary: str = Field(..., description="2-3 sentence summary of what the video is actually about")
    suggested_category: str = Field(..., description="Best-fit YouTube category, e.g. 'Comedy', 'Education'")
    confidence: float = Field(..., ge=0, le=1, description="Model's confidence that this metadata fits the content")


GENERATION_SYSTEM_PROMPT = """You are an expert YouTube SEO strategist and copywriter.
You will be given the transcript of a video. Based ONLY on what is actually said/shown
(inferred from the transcript), generate publish-ready YouTube metadata.

Rules:
- Title: under 100 characters, front-load the hook/keyword, no clickbait that misrepresents content.
- Description: 3-5 short paragraphs. Include relevant keywords naturally, a soft call-to-action
  (subscribe/comment), and do NOT invent claims not supported by the transcript.
- Tags: 10-15 relevant keyword phrases (no # symbol).
- Hashtags: 3-5 hashtags (with # symbol), relevant and not spammy.
- Pinned comment: short (1-2 sentences), inviting engagement (a question works well).
- Community post: short, casual, teases the video to drive clicks.
- Thumbnail text: max 5-6 words, high-impact, legible at small size.
- If the transcript is very short, noisy, or unclear, lower the confidence score honestly
  rather than guessing wildly.

Respond ONLY with valid JSON matching this exact shape, no markdown fences, no preamble:
{
  "title": "...",
  "description": "...",
  "tags": ["...", "..."],
  "hashtags": ["#...", "#..."],
  "pinned_comment": "...",
  "community_post": "...",
  "thumbnail_text": "...",
  "content_summary": "...",
  "suggested_category": "...",
  "confidence": 0.0
}
"""


class GeminiService:
    def __init__(self):
        settings = get_settings()
        genai.configure(api_key=settings.gemini_api_key)
        self.model_name = settings.gemini_model
        self.model = genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=GENERATION_SYSTEM_PROMPT,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def generate_metadata(self, transcript_text: str, channel_niche_hint: str | None = None) -> GeneratedContent:
        """
        Generates all video metadata from a transcript. Retries on
        transient API errors. Raises ValidationError if Gemini returns
        content that doesn't match the expected schema (caller should
        surface this as a failed pipeline step, not silently publish).
        """
        if not transcript_text or not transcript_text.strip():
            raise ValueError("Cannot generate metadata from an empty transcript.")

        prompt_parts = [f"Transcript:\n{transcript_text.strip()}"]
        if channel_niche_hint:
            prompt_parts.append(f"\nChannel context/niche: {channel_niche_hint}")

        response = self.model.generate_content(
            "\n".join(prompt_parts),
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.7,
            ),
        )

        raw_text = response.text.strip()
        try:
            data = json.loads(raw_text)
            return GeneratedContent(**data)
        except (json.JSONDecodeError, ValidationError) as e:
            raise RuntimeError(f"Gemini returned malformed metadata: {e}\nRaw response: {raw_text[:500]}")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def suggest_best_publish_time(
        self, channel_niche: str, audience_timezone: str, historical_performance: dict | None = None
    ) -> dict:
        """
        Asks Gemini to reason about the best upload time given channel
        niche + (optionally) historical analytics data. This is a
        heuristic suggestion, not a guarantee — the analytics service
        should refine this further once real performance data exists.
        """
        prompt = f"""Given a YouTube channel in the niche "{channel_niche}" with an audience
mostly in timezone {audience_timezone}, suggest the single best day-of-week and
time-of-day to publish a new video for maximum initial reach.
"""
        if historical_performance:
            prompt += f"\nHistorical performance data (JSON): {json.dumps(historical_performance)}"

        prompt += """
Respond ONLY with valid JSON, no markdown fences:
{
  "day_of_week": "Monday",
  "hour_24": 18,
  "timezone": "...",
  "reasoning": "1-2 sentence explanation"
}
"""
        response = self.model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(response_mime_type="application/json", temperature=0.4),
        )
        return json.loads(response.text.strip())
