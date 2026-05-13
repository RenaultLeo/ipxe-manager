import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import settings

logger = logging.getLogger(__name__)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables then add any missing columns (safe to call multiple times)."""
    from app.models import models  # noqa: F401 — registers models with Base.metadata
    Base.metadata.create_all(bind=engine)
    _migrate_columns()


def _migrate_columns():
    """Add columns introduced after initial deploy without needing Alembic."""
    _add_column_if_missing("boot_entries", "bootmgr_path",      "VARCHAR(512)")
    _add_column_if_missing("boot_entries", "bcd_path",           "VARCHAR(512)")
    _add_column_if_missing("boot_entries", "boot_sdi_path",      "VARCHAR(512)")
    _add_column_if_missing("boot_entries", "efi_path",           "VARCHAR(512)")
    _add_column_if_missing("boot_entries",  "modloop_path",       "VARCHAR(512)")
    _add_column_if_missing("os_types",      "is_builtin",         "BOOLEAN DEFAULT 0")
    _add_column_if_missing("boot_entries", "custom_ipxe_path",   "VARCHAR(512)")
    # remote_chains table est créée via Base.metadata.create_all — pas besoin d'ALTER


def _add_column_if_missing(table: str, column: str, col_type: str):
    with engine.connect() as conn:
        try:
            # SQLite: check if column exists via PRAGMA
            result = conn.execute(text(f"PRAGMA table_info({table})"))
            columns = [row[1] for row in result.fetchall()]
            if column not in columns:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                conn.commit()
                logger.info("Migration : colonne '%s.%s' ajoutée", table, column)
        except Exception:
            logger.exception("Migration échouée pour %s.%s", table, column)
