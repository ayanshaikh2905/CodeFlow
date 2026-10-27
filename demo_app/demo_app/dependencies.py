"""Dependencies shared by API routes."""

from collections.abc import AsyncGenerator

import httpx
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from demo_app.database import get_session
from demo_app.models import User


def get_current_user(
    x_user_id: int = Header(...), session: Session = Depends(get_session)
) -> User:
    """Authenticate a demo request using its X-User-ID header."""
    user = session.get(User, x_user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Unknown demo user")
    return user


async def get_http_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Provide a real close-after-request HTTP client."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        yield client
