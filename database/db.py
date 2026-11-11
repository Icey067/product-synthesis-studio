import sqlite3
import logging
from pathlib import Path
from contextlib import contextmanager
from config.settings import DATABASE_PATH, DEFAULT_CHAPTER_TARGET_WORDS, DEFAULT_MIN_CHAPTER_WORDS
from database.migrations import apply_migrations, backup_database, pending_migrations

logger = logging.getLogger(__name__)

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DATABASE_PATH), timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

@contextmanager
def transaction():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Database transaction rolled back: {e}")
        raise
    finally:
        conn.close()

def init_db():
    schema_path = Path(__file__).resolve().parent / "schema.sql"
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    # Detect pending structural migrations on an existing database and back it
    # up before any DDL runs. Fresh databases need no backup.
    needs_backup = False
    if DATABASE_PATH.exists() and DATABASE_PATH.stat().st_size > 0:
        probe = sqlite3.connect(str(DATABASE_PATH))
        try:
            pending = pending_migrations(probe)
            needs_backup = bool(pending)
        except Exception:
            needs_backup = True
        finally:
            probe.close()

    if needs_backup:
        backup_database("pre_migration")

    with transaction() as conn:
        conn.executescript(schema_sql)
        applied = apply_migrations(conn)
        if applied:
            logger.info("Applied database migrations: %s", applied)

        # Seed default settings if empty
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM application_settings")
        if cur.fetchone()[0] == 0:
            default_settings = [
                ("ollama_url", "http://localhost:11434"),
                # Intentionally empty: the installed model is detected at
                # runtime instead of assuming one is present.
                ("selected_model", ""),
                ("context_window", "8192"),
                ("temperature", "0.7"),
                ("keep_alive", "5m"),
                ("theme", "dark"),
                ("chapter_target_words", str(DEFAULT_CHAPTER_TARGET_WORDS)),
                ("min_chapter_words", str(DEFAULT_MIN_CHAPTER_WORDS)),
            ]
            cur.executemany("INSERT INTO application_settings (key, value) VALUES (?, ?)", default_settings)
        else:
            cur.execute(
                "INSERT OR IGNORE INTO application_settings (key, value) VALUES ('chapter_target_words', ?)",
                (str(DEFAULT_CHAPTER_TARGET_WORDS),),
            )
            cur.execute(
                "INSERT OR IGNORE INTO application_settings (key, value) VALUES ('min_chapter_words', ?)",
                (str(DEFAULT_MIN_CHAPTER_WORDS),),
            )
    logger.info("Database initialized successfully.")

if __name__ == "__main__":
    init_db()
    print("Database initialization complete.")
