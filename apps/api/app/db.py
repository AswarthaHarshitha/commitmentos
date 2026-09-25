"""Engine / session plumbing.

Unit-of-work rule: services only ``flush()``; the *route handler* (or the n8n webhook
handler) calls ``db.commit()`` once the whole operation succeeded. We deliberately do not
commit in a yield-dependency teardown: FastAPI may run teardown after the response has been
sent, which would let a fast client read stale data.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engine: Engine | None = None
_sessionmaker: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_settings().database_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=10,
            pool_recycle=1800,
            connect_args={"options": "-c timezone=UTC"},  # datetimes always come back as UTC
        )
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=True)
    return _sessionmaker


def reset_engine() -> None:
    """Dispose the engine (used by tests when DATABASE_URL changes)."""
    global _engine, _sessionmaker
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _sessionmaker = None


def get_db() -> Iterator[Session]:
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()  # rolls back anything not explicitly committed
