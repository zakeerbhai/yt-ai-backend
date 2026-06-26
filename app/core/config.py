"""
Centralized application settings.

All secrets are loaded from environment variables (via a local `.env` file
in development, or real environment injection in production/CI). Nothing
secret is hardcoded here or anywhere else in the codebase.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_env: str = "development"
    app_secret_key: str
    app_base_url: str = "http://localhost:8000"
    frontend_base_url: str = "http://localhost:5173"

    # --- Database ---
    database_url: str

    # --- Google OAuth / YouTube ---
    google_oauth_client_id: str
    google_oauth_client_secret: str
    google_oauth_redirect_uri: str
    youtube_data_api_key: str

    # --- Gemini ---
    gemini_api_key: str
    gemini_model: str = "gemini-2.0-flash"

    # --- AssemblyAI ---
    assemblyai_api_key: str

    # --- Cloudinary ---
    cloudinary_cloud_name: str
    cloudinary_api_key: str
    cloudinary_api_secret: str

    # --- Firebase ---
    firebase_service_account_json_path: str = "./secrets/firebase-service-account.json"
    firebase_project_id: str

    # --- Redis / Celery ---
    redis_url: str = "redis://localhost:6379/0"

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> "Settings":
    """
    Cached settings accessor. Raises a clear pydantic ValidationError at
    startup (not at first request) if required env vars are missing, so
    misconfiguration fails fast instead of causing confusing runtime bugs.
    """
    return Settings()
