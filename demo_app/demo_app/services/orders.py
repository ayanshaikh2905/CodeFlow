"""Order and payment business logic."""

from collections.abc import Callable
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload

from demo_app.models import Order, OrderItem, Product, User
from demo_app.schemas import OrderCreate
from demo_app.services.audit import audit_log


@audit_log
def create_order(session: Session, user: User, payload: OrderCreate) -> Order:
    """Create an order after loading its products from the database."""
    order = Order(user_id=user.id, status="pending", total=0.0)
    for requested_item in payload.items:
        product = session.get(Product, requested_item.product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found")
        order.items.append(
            OrderItem(
                product=product,
                quantity=requested_item.quantity,
                unit_price=product.price,
            )
        )
        order.total += product.price * requested_item.quantity
    session.add(order)
    session.commit()
    session.refresh(order)
    return order


def get_order(session: Session, order_id: int, user_id: int) -> Order:
    """Load an order and its items, scoped to the owning user."""
    order = (
        session.query(Order)
        .options(selectinload(Order.items))
        .filter(Order.id == order_id, Order.user_id == user_id)
        .first()
    )
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


async def charge_with_httpx(client: httpx.AsyncClient, order: Order) -> dict[str, Any]:
    """Call the external payment endpoint."""
    response = await client.post(
        "https://payments.example.test/charges",
        json={"order_id": order.id, "amount": order.total},
    )
    response.raise_for_status()
    return response.json()


PaymentHandler = Callable[[httpx.AsyncClient, Order], Any]
PAYMENT_PROVIDERS: dict[str, PaymentHandler] = {"card": charge_with_httpx}


async def pay_order(
    session: Session,
    client: httpx.AsyncClient,
    order_id: int,
    user_id: int,
    provider: str = "card",
) -> tuple[Order, str]:
    """Dispatch to a provider selected at runtime, then update the order."""
    order = get_order(session, order_id, user_id)
    if order.status == "paid":
        raise HTTPException(status_code=409, detail="Order already paid")
    handler = PAYMENT_PROVIDERS.get(provider)
    if handler is None:
        raise HTTPException(status_code=400, detail="Unknown payment provider")
    # Registry-based dispatch is intentionally dynamic for the runtime overlay demo.
    result = await handler(client, order)
    order.status = "paid"
    session.commit()
    return order, str(result.get("id", "demo-transaction"))
