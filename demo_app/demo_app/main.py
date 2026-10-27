"""Application factory and local development entry point."""

from fastapi import FastAPI
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from demo_app.database import engine as default_engine
from demo_app.models import Base, Product, User
from demo_app.routes import router


def create_app(engine: Engine = default_engine, create_schema: bool = True) -> FastAPI:
    """Build the demo API, optionally creating its schema."""
    if create_schema:
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        with factory() as session:
            if session.query(User).first() is None:
                session.add_all(
                    [
                        User(id=1, email="ada@example.test", name="Ada Lovelace"),
                        Product(id=1, name="CodeFlow Keyboard", price=80.0),
                        Product(id=2, name="CodeFlow Mouse", price=25.0),
                    ]
                )
                session.commit()
    app = FastAPI(title="CodeFlow Demo API", version="0.1.0")
    app.include_router(router)
    app.state.engine = engine
    return app


app = create_app()
