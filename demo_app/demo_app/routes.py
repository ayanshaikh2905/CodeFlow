"""FastAPI routes for users and orders."""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from demo_app.database import get_session
from demo_app.dependencies import get_current_user, get_http_client
from demo_app.models import User
from demo_app.schemas import OrderCreate, OrderRead, PaymentRead, UserRead
from demo_app.services.orders import create_order, get_order, pay_order

router = APIRouter()


@router.get("/users/{user_id}", response_model=UserRead, tags=["users"])
def read_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> User:
    """Return a user record to an authenticated demo user."""
    if current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Cannot read another user")
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/orders", response_model=OrderRead, status_code=201, tags=["orders"])
def post_order(
    payload: OrderCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> OrderRead:
    """Create an order for the authenticated user."""
    return create_order(session, current_user, payload)


@router.get("/orders/{order_id}", response_model=OrderRead, tags=["orders"])
def read_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> OrderRead:
    """Fetch one order belonging to the authenticated user."""
    return get_order(session, order_id, current_user.id)


@router.post("/orders/{order_id}/pay", response_model=PaymentRead, tags=["orders"])
async def post_payment(
    order_id: int,
    provider: str = Query(default="card"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> PaymentRead:
    """Pay an order using a dynamically selected provider."""
    order, transaction_id = await pay_order(
        session, client, order_id, current_user.id, provider
    )
    return PaymentRead(
        order_id=order.id,
        status=order.status,
        provider=provider,
        transaction_id=transaction_id,
    )
