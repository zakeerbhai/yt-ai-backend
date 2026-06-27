"""
Firebase Admin SDK initialization.
"""
import base64
import json
import os
from functools import lru_cache

import firebase_admin
from firebase_admin import credentials

from app.core.config import get_settings


@lru_cache
def get_firebase_app() -> firebase_admin.App:
    settings = get_settings()

    json_str = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    if json_str:
        # Try base64 decode first (safer for Railway env vars)
        try:
            decoded = base64.b64decode(json_str).decode("utf-8")
            service_account_info = json.loads(decoded)
        except Exception:
            # Fall back to raw JSON
            service_account_info = json.loads(json_str)
        cred = credentials.Certificate(service_account_info)
    else:
        path = settings.firebase_service_account_json_path
        if not os.path.exists(path):
            raise RuntimeError(
                f"Firebase service account file not found at '{path}'."
            )
        cred = credentials.Certificate(path)

    return firebase_admin.initialize_app(
        cred, {"projectId": settings.firebase_project_id}
    )
