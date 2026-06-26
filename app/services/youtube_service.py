"""
YouTube Data API v3 + YouTube Analytics API integration.

Uses per-channel OAuth credentials (access + refresh token), NOT a bare
API key, because uploading/scheduling/updating videos requires write
access to a specific authorized channel. The API key alone (read-only,
public data) is insufficient for these write operations — this is a
deliberate design choice, not an oversight.
"""
from datetime import datetime, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from app.core.config import get_settings

YOUTUBE_UPLOAD_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


class YouTubeService:
    def __init__(self, access_token: str, refresh_token: str):
        settings = get_settings()
        self.credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.google_oauth_client_id,
            client_secret=settings.google_oauth_client_secret,
            scopes=YOUTUBE_UPLOAD_SCOPES,
        )
        self.youtube = build("youtube", "v3", credentials=self.credentials)
        self.youtube_analytics = build("youtubeAnalytics", "v2", credentials=self.credentials)

    # --- Upload / Publish ---

    def upload_video(
        self,
        file_path: str,
        title: str,
        description: str,
        tags: list[str],
        category_id: str = "22",
        privacy_status: str = "private",
        publish_at: datetime | None = None,
    ) -> dict:
        """
        Uploads a video. If publish_at is set, the video is uploaded as
        private and scheduled to go public at that time (YouTube handles
        the actual publish transition). Otherwise privacy_status applies
        immediately.
        """
        status = {"privacyStatus": "private" if publish_at else privacy_status, "selfDeclaredMadeForKids": False}
        if publish_at:
            status["publishAt"] = publish_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

        body = {
            "snippet": {
                "title": title[:100],
                "description": description,
                "tags": tags,
                "categoryId": category_id,
            },
            "status": status,
        }

        media = MediaFileUpload(file_path, chunksize=-1, resumable=True, mimetype="video/*")
        request = self.youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            status_progress, response = request.next_chunk()
        return response

    def set_thumbnail(self, video_id: str, thumbnail_path: str) -> dict:
        return self.youtube.thumbnails().set(
            videoId=video_id, media_body=MediaFileUpload(thumbnail_path)
        ).execute()

    def update_video_metadata(
        self,
        video_id: str,
        title: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        category_id: str | None = None,
    ) -> dict:
        existing = self.youtube.videos().list(part="snippet", id=video_id).execute()
        if not existing["items"]:
            raise ValueError(f"Video {video_id} not found on this channel.")
        snippet = existing["items"][0]["snippet"]

        if title is not None:
            snippet["title"] = title[:100]
        if description is not None:
            snippet["description"] = description
        if tags is not None:
            snippet["tags"] = tags
        if category_id is not None:
            snippet["categoryId"] = category_id

        return self.youtube.videos().update(
            part="snippet", body={"id": video_id, "snippet": snippet}
        ).execute()

    def add_video_to_playlist(self, playlist_id: str, video_id: str) -> dict:
        return self.youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        ).execute()

    def post_pinned_comment(self, video_id: str, comment_text: str) -> dict:
        """
        Posts a top-level comment. NOTE: the YouTube Data API does not
        support programmatically "pinning" a comment — pinning must be
        done manually in YouTube Studio or via the creator. This method
        posts the comment so it's ready to be pinned.
        """
        return self.youtube.commentThreads().insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {"snippet": {"textOriginal": comment_text}},
                }
            },
        ).execute()

    # --- Channel info ---

    def get_my_channel(self) -> dict:
        response = self.youtube.channels().list(part="snippet,statistics,contentDetails", mine=True).execute()
        if not response["items"]:
            raise ValueError("No YouTube channel found for these credentials.")
        return response["items"][0]

    # --- Analytics ---

    def get_channel_analytics(
        self, channel_youtube_id: str, start_date: str, end_date: str
    ) -> dict:
        """
        Pulls aggregate channel analytics (views, watch time, subscribers
        gained, CTR) for a date range, e.g. start_date="2026-05-01".
        """
        return self.youtube_analytics.reports().query(
            ids=f"channel=={channel_youtube_id}",
            startDate=start_date,
            endDate=end_date,
            metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
                    "subscribersGained,subscribersLost,likes,comments,impressions,impressionsClickThroughRate",
            dimensions="day",
            sort="day",
        ).execute()

    def get_video_analytics(self, channel_youtube_id: str, video_id: str, start_date: str, end_date: str) -> dict:
        return self.youtube_analytics.reports().query(
            ids=f"channel=={channel_youtube_id}",
            startDate=start_date,
            endDate=end_date,
            metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
                    "likes,comments,subscribersGained,impressions,impressionsClickThroughRate",
            filters=f"video=={video_id}",
        ).execute()
