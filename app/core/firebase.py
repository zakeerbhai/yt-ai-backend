"""
Firebase Admin SDK initialization. Used for:
  - Verifying ID tokens sent by the frontend (Authorization: Bearer <token>)
  - Sending push notifications (FCM) — see services/notifications.py

Initialized once, lazily, from the service account JSON path in settings.
"""
import os
from functools import lru_cache

import firebase_admin
from firebase_admin import credentials

from app.core.config import get_settings


@lru_cache
def get_firebase_app() -> firebase_admin.App:
    settings = get_settings()
    path = settings.firebase_service_account_json_path

    if not os.path.exists(path):
        raise RuntimeError(
            f"Firebase service account file not found at '{path}'. "
            "Download it from Firebase Console > Project Settings > "
            "Service Accounts > Generate new private key, and set "
            "FIREBASE_SERVICE_ACCOUNT_JSON_PATH accordingly. Never commit this file."
        )

    cred = credentials.Certificate(path)
    return firebase_admin.initialize_app(cred, {"projectId": settings.firebase_project_id})
