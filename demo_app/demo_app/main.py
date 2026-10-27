"""Application factory and local development entry point."""

from fastapi import FastAPI
from sqlalchemy.engine import Engine

from demo_app.database import engine as default_engine
from demo_app.models import Base
from demo_app.routes import router


def create_app(engine: Engine = default_engine, create_schema: bool = True) -> FastAPI:
    """Build the demo API, optionally creating its schema."""
    if create_schema:
        Base.metadata.create_all(engine)
    app = FastAPI(title="CodeFlow Demo API", version="0.1.0")
    app.include_router(router)
    app.state.engine = engine
    return app


app = create_app()
