from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings

connect_args = {}
engine_kwargs = {}

if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}
    if ":memory:" in settings.DATABASE_URL or settings.DATABASE_URL.endswith("sqlite://"):
        engine_kwargs["poolclass"] = StaticPool

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    **engine_kwargs,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Yield a database session for a single request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create database tables if they do not already exist."""
    # Import models so they are registered on Base.metadata.
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    ensure_chunk_columns()


def ensure_chunk_columns() -> None:
    """Add chunk metadata columns on existing SQLite databases."""
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    with engine.begin() as conn:
        rows = conn.exec_driver_sql("PRAGMA table_info(chunks)").fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        statements = []
        if "page_start" not in existing:
            statements.append("ALTER TABLE chunks ADD COLUMN page_start INTEGER")
        if "page_end" not in existing:
            statements.append("ALTER TABLE chunks ADD COLUMN page_end INTEGER")
        if "section" not in existing:
            statements.append("ALTER TABLE chunks ADD COLUMN section VARCHAR DEFAULT ''")
        for sql in statements:
            conn.exec_driver_sql(sql)
