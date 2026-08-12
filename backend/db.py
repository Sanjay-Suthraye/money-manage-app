from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.settings import DB_PATH

DB_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


_USER_TABLES = ("accounts", "categories", "persons", "import_batches",
                "transactions", "budgets", "recurring_rules", "conversations")


def run_migrations() -> None:
    """Additive column migrations create_all can't do. Idempotent; called from
    the app lifespan after create_all."""
    from sqlalchemy import text

    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(chat_messages)"))}
        if cols and "conversation_id" not in cols:
            conn.execute(text(
                "ALTER TABLE chat_messages ADD COLUMN conversation_id INTEGER "
                "REFERENCES conversations(id)"
            ))
        for table in _USER_TABLES:
            cols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            if cols and "user_id" not in cols:
                conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN user_id INTEGER REFERENCES users(id)"
                ))
                conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_user_id ON {table}(user_id)"
                ))
