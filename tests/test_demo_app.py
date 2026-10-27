"""Behavior checks for the demo API and its persistence/external-call paths."""

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from demo_app.database import get_session, make_engine
from demo_app.dependencies import get_http_client
from demo_app.main import create_app
from demo_app.models import Base, Product, User


@pytest.fixture
def client() -> TestClient:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        session.add_all(
            [
                User(id=1, email="ada@example.test", name="Ada"),
                Product(id=1, name="Keyboard", price=80.0),
                Product(id=2, name="Mouse", price=25.0),
            ]
        )
        session.commit()

    app = create_app(engine, create_schema=False)

    def session_override():
        session: Session = factory()
        try:
            yield session
        finally:
            session.close()

    async def http_override():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"id": "charge-123"})
        )
        async with httpx.AsyncClient(transport=transport) as http_client:
            yield http_client

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_http_client] = http_override
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()


def test_routes_create_and_read_order(client: TestClient) -> None:
    headers = {"X-User-ID": "1"}
    response = client.post(
        "/orders",
        headers=headers,
        json={
            "items": [
                {"product_id": 1, "quantity": 2},
                {"product_id": 2, "quantity": 1},
            ]
        },
    )
    assert response.status_code == 201
    order = response.json()
    assert order["total"] == 185.0
    assert len(order["items"]) == 2

    fetched = client.get(f"/orders/{order['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "pending"


def test_user_route_requires_auth_and_checks_identity(client: TestClient) -> None:
    assert client.get("/users/1").status_code == 422
    assert client.get("/users/1", headers={"X-User-ID": "1"}).json()["name"] == "Ada"
    assert client.get("/users/2", headers={"X-User-ID": "1"}).status_code == 403


def test_pay_order_uses_mocked_httpx_call(client: TestClient) -> None:
    created = client.post(
        "/orders",
        headers={"X-User-ID": "1"},
        json={"items": [{"product_id": 1, "quantity": 1}]},
    )
    order_id = created.json()["id"]
    payment = client.post(f"/orders/{order_id}/pay", headers={"X-User-ID": "1"})
    assert payment.status_code == 200
    assert payment.json() == {
        "order_id": order_id,
        "status": "paid",
        "provider": "card",
        "transaction_id": "charge-123",
    }
    duplicate = client.post(f"/orders/{order_id}/pay", headers={"X-User-ID": "1"})
    assert duplicate.status_code == 409
