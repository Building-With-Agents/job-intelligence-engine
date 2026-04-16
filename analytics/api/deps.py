"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Generator

import structlog
from sqlalchemy.orm import Session

from common.data_store.database import get_session_factory

log = structlog.get_logger()


def get_db_session() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session (commit on success)."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
