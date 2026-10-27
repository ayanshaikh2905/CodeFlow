"""Dependencies shared by API routes."""

import os
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
    """Provide a close-after-request HTTP client; tests replace its transport."""
    if os.environ.get("CODEFLOW_DEMO_MOCK_PAYMENTS") == "1":
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"id": "demo-charge"})
        )
        async with httpx.AsyncClient(timeout=5.0, transport=transport) as client:
            yield client
    else:
        async with httpx.AsyncClient(timeout=5.0) as client:
            yield client
