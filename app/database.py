"""
JalSense 2.0 — Database Setup

SQLite with WAL mode for concurrent read safety. SQLAlchemy ORM
with synchronous engine (sufficient for hackathon scale).

WAL mode allows multiple readers while one writer is active,
which prevents the 'database is locked' error during concurrent
webhook processing.
"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from app.config import get_settings

settings = get_settings()

# Create engine with SQLite-specific settings
engine = create_engine(
    settings.database_url,
    connect_args={
        "check_same_thread": False,  # Allow FastAPI threads to share connection
        "timeout": 30,               # Wait up to 30s for write lock
    },
    echo=False,  # Set True for SQL debug logging
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """
    Enable WAL mode and other performance pragmas on every new connection.
    WAL = Write-Ahead Logging: allows concurrent reads during writes.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")  # Faster writes, still crash-safe with WAL
    cursor.execute("PRAGMA busy_timeout=30000")   # 30s wait on lock contention
    cursor.close()


# Session factory
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""
    pass


def init_database():
    """Create all tables if they don't exist. Called once at startup."""
    # Import models so they register with Base.metadata
    import app.models.farmer  # noqa: F401
    import app.models.alert   # noqa: F401
    Base.metadata.create_all(bind=engine)


def get_db() -> Session:
    """
    Dependency for FastAPI routes that need a database session.
    Usage: db: Session = Depends(get_db)
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
