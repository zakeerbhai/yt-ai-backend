"""
YouTube OAuth connect flow.

  GET /api/auth/youtube/connect
      -> redirects the browser to Google's consent screen

  GET /api/auth/youtube/callback
      -> Google redirects back here with a `code`; we exchange it for
         tokens, fetch the channel's identity via the Data API, encrypt
         + store the tokens, and create/update the Channel row.

State/CSRF handling: we sign a short-lived state token containing the
requesting user's id, so the callback (which has no Authorization
header — it's a browser redirect from Google) knows which user to
attach the channel to, and we can verify the request wasn't forged.
"""
import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.database import get_db
from app.models.user import User, Channel, AutoPublishMode
from app.services.token_cipher import TokenCipher
from app.services.youtube_service import YouTubeService, YOUTUBE_UPLOAD_SCOPES

router = APIRouter(prefix="/api/auth/youtube", tags=["auth"])

STATE_MAX_AGE_SECONDS = 600  # 10 minutes to complete the OAuth round trip


def _serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    return URLSafeTimedSerializer(settings.app_secret_key, salt="youtube-oauth-state")


def _build_flow() -> Flow:
    settings = get_settings()
    client_config = {
        "web": {
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_oauth_redirect_uri],
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=YOUTUBE_UPLOAD_SCOPES,
        redirect_uri=settings.google_oauth_redirect_uri,
    )


@router.get("/connect")
async def connect_youtube(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Step 1: redirect the logged-in user to Google's consent screen.
    Accepts the Firebase ID token as a URL parameter since browser
    redirects cannot send Authorization headers.
    """
    # Verify the token manually since we can't use the normal dependency
    from firebase_admin import auth as firebase_auth
    from app.models.user import User
    from sqlalchemy import select

    get_firebase_app()
    try:
        decoded = firebase_auth.verify_id_token(token)
    except Exception:
        raise HTTPException(401, "Invalid or expired token. Please log in again.")

    firebase_uid = decoded["uid"]
    result = await db.execute(select(User).where(User.firebase_uid == firebase_uid))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found.")

    flow = _build_flow()
    state = _serializer().dumps({"user_id": str(user.id), "ts": time.time()})
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return RedirectResponse(auth_url)


@router.get("/callback")
async def youtube_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Step 2: Google redirects here with an authorization code. We:
      1. Verify the signed state token (CSRF protection + recover user_id)
      2. Exchange the code for access/refresh tokens
      3. Look up the channel identity via the Data API
      4. Encrypt tokens, upsert the Channel row
      5. Redirect back to the frontend
    """
    settings = get_settings()

    try:
        payload = _serializer().loads(state, max_age=STATE_MAX_AGE_SECONDS)
    except SignatureExpired:
        raise HTTPException(400, "OAuth session expired. Please try connecting again.")
    except BadSignature:
        raise HTTPException(400, "Invalid OAuth state. Possible CSRF attempt — request rejected.")

    user_id = uuid.UUID(payload["user_id"])
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found.")

    flow = _build_flow()
    flow.fetch_token(code=code)
    credentials = flow.credentials

    if not credentials.refresh_token:
        # Happens if the user previously granted access and Google
        # didn't re-issue a refresh_token despite prompt=consent in some
        # edge cases. Without it we can't keep the connection alive past
        # the first hour, so treat this as a hard failure.
        raise HTTPException(
            400,
            "Google did not return a refresh token. Please revoke this app's access at "
            "https://myaccount.google.com/permissions and try connecting again.",
        )

    yt = YouTubeService(access_token=credentials.token, refresh_token=credentials.refresh_token)
    channel_info = yt.get_my_channel()
    youtube_channel_id = channel_info["id"]
    channel_title = channel_info["snippet"]["title"]
    channel_thumb = channel_info["snippet"]["thumbnails"]["default"]["url"]

    cipher = TokenCipher()
    encrypted_access = cipher.encrypt(credentials.token)
    encrypted_refresh = cipher.encrypt(credentials.refresh_token)

    result = await db.execute(select(Channel).where(Channel.youtube_channel_id == youtube_channel_id))
    channel = result.scalar_one_or_none()

    if channel:
        if channel.owner_id != user.id:
            raise HTTPException(
                409,
                "This YouTube channel is already connected to a different account.",
            )
        channel.access_token_encrypted = encrypted_access
        channel.refresh_token_encrypted = encrypted_refresh
        channel.token_expiry = credentials.expiry
        channel.title = channel_title
        channel.thumbnail_url = channel_thumb
        channel.is_active = True
    else:
        channel = Channel(
            owner_id=user.id,
            youtube_channel_id=youtube_channel_id,
            title=channel_title,
            thumbnail_url=channel_thumb,
            access_token_encrypted=encrypted_access,
            refresh_token_encrypted=encrypted_refresh,
            token_expiry=credentials.expiry,
            auto_publish_mode=AutoPublishMode.MANUAL_REVIEW,  # safe default
        )
        db.add(channel)

    await db.commit()
    await db.refresh(channel)

    return RedirectResponse(f"{settings.frontend_base_url}/settings?youtube_connected={channel.id}")


@router.delete("/{channel_id}")
async def disconnect_youtube(
    channel_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    channel = await db.get(Channel, channel_id)
    if not channel or channel.owner_id != user.id:
        raise HTTPException(404, "Channel not found.")
    channel.is_active = False
    await db.commit()
    return {"message": "Channel disconnected."}
