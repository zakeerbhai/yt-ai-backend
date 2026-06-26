"""
Firebase Admin SDK initialization.
Supports two modes:
1. Local development: reads from a service account JSON file
2. Production (Railway): reads from FIREBASE_SERVICE_ACCOUNT_JSON environment variable
"""
import json
import os
from functools import lru_cache

import firebase_admin
from firebase_admin import credentials

from app.core.config import get_settings


@lru_cache
def get_firebase_app() -> firebase_admin.App:
    settings = get_settings()

    # Production: read from environment variable (Railway)
    json_str = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    if json_str:
        service_account_info = json.loads(json_str)
        cred = credentials.Certificate(service_account_info)
    else:
        # Local development: read from file
        path = settings.firebase_service_account_json_path
        if not os.path.exists(path):
            raise RuntimeError(
                f"Firebase service account file not found at '{path}'. "
                "Either set FIREBASE_SERVICE_ACCOUNT_JSON environment variable "
                "or provide the file path in settings."
            )
        cred = credentials.Certificate(path)

    return firebase_admin.initialize_app(
        cred, {"projectId": settings.firebase_project_id}
    )
