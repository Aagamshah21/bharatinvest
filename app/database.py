from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30} if "sqlite" in settings.DATABASE_URL else {}
)

if "sqlite" in settings.DATABASE_URL:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def run_db_migrations(target_engine=None):
    """
    Idempotent database migration for SQLite and PostgreSQL:
    Inspects existing columns on the 'users' table and runs safe ALTER TABLE statements
    if columns are missing, preserving all existing user data.
    """
    if target_engine is None:
        target_engine = engine
    from sqlalchemy import inspect, text
    inspector = inspect(target_engine)
    table_names = inspector.get_table_names()
    if "users" in table_names:
        columns = {c["name"] for c in inspector.get_columns("users")}
        with target_engine.begin() as conn:
            if "google_sub" not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN google_sub VARCHAR(100)"))
            if "picture_url" not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN picture_url VARCHAR(500)"))
            if "auth_provider" not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN auth_provider VARCHAR(20) DEFAULT 'local'"))
            if "last_login_at" not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP"))

# Execute safe migrations on startup for default engine
try:
    Base.metadata.create_all(bind=engine)
    run_db_migrations(engine)
except Exception as e:
    pass


