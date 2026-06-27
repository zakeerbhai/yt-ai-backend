from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import get_settings
from app.core.firebase import get_firebase_app
from app.api import videos, auth, users


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings()
    get_firebase_app()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="YouTube AI Automation Manager",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(videos.router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
