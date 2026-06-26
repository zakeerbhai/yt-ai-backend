"""
Auth dependency: verifies a Firebase ID token from the Authorization
header, then resolves (or lazily creates) the corresponding local User
row. Every protected route depends on `get_current_user`.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth as firebase_auth
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.firebase import get_firebase_app
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Expected: Bearer <firebase_id_token>",
        )

    get_firebase_app()  # ensures Firebase Admin is initialized

    try:
        decoded = firebase_auth.verify_id_token(credentials.credentials)
    except firebase_auth.ExpiredIdTokenError:
        raise HTTPException(status_code=401, detail="Firebase ID token has expired. Sign in again.")
    except firebase_auth.InvalidIdTokenError:
        raise HTTPException(status_code=401, detail="Invalid Firebase ID token.")
    except Exception:
        raise HTTPException(status_code=401, detail="Could not verify credentials.")

    firebase_uid = decoded["uid"]
    email = decoded.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="Firebase account has no email on file.")

    result = await db.execute(select(User).where(User.firebase_uid == firebase_uid))
    user = result.scalar_one_or_none()

    if user is None:
        # First time this Firebase identity has hit the API — provision
        # a local User row (this is "sign up" from the backend's POV).
        user = User(
            firebase_uid=firebase_uid,
            email=email,
            display_name=decoded.get("name"),
            avatar_url=decoded.get("picture"),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    return user
