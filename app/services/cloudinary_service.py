"""
Cloudinary integration: upload/store source videos and AI-suggested
thumbnails, with automatic media optimization.
"""
import cloudinary
import cloudinary.uploader
from functools import lru_cache

from app.core.config import get_settings


@lru_cache
def _configure_cloudinary():
    settings = get_settings()
    cloudinary.config(
        cloud_name=settings.cloudinary_cloud_name,
        api_key=settings.cloudinary_api_key,
        api_secret=settings.cloudinary_api_secret,
        secure=True,
    )
    return True


class CloudinaryService:
    def __init__(self):
        _configure_cloudinary()

    def upload_video(self, file_path: str, public_id: str) -> dict:
        """
        Uploads a video file to Cloudinary with automatic quality/format
        optimization. Returns the secure URL, public_id, and duration.
        """
        result = cloudinary.uploader.upload_large(
            file_path,
            resource_type="video",
            public_id=public_id,
            folder="yt_ai_manager/videos",
            eager=[{"quality": "auto", "fetch_format": "auto"}],
            eager_async=True,
        )
        return {
            "secure_url": result["secure_url"],
            "public_id": result["public_id"],
            "duration": result.get("duration"),
            "format": result.get("format"),
            "bytes": result.get("bytes"),
        }

    def upload_thumbnail(self, file_path_or_url: str, public_id: str) -> dict:
        """Uploads/optimizes a thumbnail image."""
        result = cloudinary.uploader.upload(
            file_path_or_url,
            resource_type="image",
            public_id=public_id,
            folder="yt_ai_manager/thumbnails",
            quality="auto",
            fetch_format="auto",
        )
        return {"secure_url": result["secure_url"], "public_id": result["public_id"]}

    def generate_thumbnail_from_video(self, video_public_id: str, second: float = 1.0) -> str:
        """
        Generates a thumbnail URL by grabbing a frame from the uploaded
        video at the given timestamp, using Cloudinary's video-to-image
        transformation (no extra upload needed).
        """
        url, _ = cloudinary.utils.cloudinary_url(
            video_public_id,
            resource_type="video",
            format="jpg",
            start_offset=str(second),
            quality="auto",
        )
        return url

    def delete_video(self, public_id: str) -> None:
        cloudinary.uploader.destroy(public_id, resource_type="video")
