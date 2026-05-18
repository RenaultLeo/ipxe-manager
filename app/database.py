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
    _add_column_if_missing("boot_entries",  "alpine_repo_url",    "VARCHAR(512)")
    _add_column_if_missing("os_types",      "is_builtin",         "BOOLEAN DEFAULT 0")
    _add_column_if_missing("boot_entries", "custom_ipxe_path",   "VARCHAR(512)")
    _add_column_if_missing("boot_entries", "extra_linux_paths_json", "TEXT DEFAULT '[]'")
    _add_column_if_missing("boot_entries", "esxi_boot_cfg_path", "VARCHAR(512)")
    _add_column_if_missing("boot_entries", "esxi_boot_cfg_legacy_path", "VARCHAR(512)")
    _add_column_if_missing("boot_entries", "esxi_efi_boot_path", "VARCHAR(512)")
    _add_column_if_missing("boot_entries", "esxi_modules",       "TEXT DEFAULT ''")
    _add_column_if_missing("boot_entries", "esxi_modules_legacy", "TEXT DEFAULT ''")
    _add_column_if_missing("autoconfigs",  "meta_data_content",  "TEXT DEFAULT ''")
    _add_column_if_missing("autoconfigs",  "ubuntu_cloud_slug",  "VARCHAR(128)")
    _add_column_if_missing("iso_versions", "iso_was_extracted", "BOOLEAN DEFAULT 0")
    _add_column_if_missing("iso_versions", "delete_iso_after_next_extract", "BOOLEAN DEFAULT 0")
    _add_column_if_missing("iso_versions", "extract_basename_report_json", "TEXT DEFAULT ''")
    _add_column_if_missing("os_types", "extract_full_iso", "BOOLEAN DEFAULT 0")
    _add_column_if_missing("os_types", "extract_paths_json", "TEXT DEFAULT '[]'")
    _add_column_if_missing("os_types", "ipxe_roles_json", "TEXT DEFAULT '[]'")
    _add_column_if_missing("os_types", "forced_autoconfig_type", "VARCHAR(64)")
    order_added = _add_column_if_missing("os_types", "ui_sort_order", "INTEGER DEFAULT 0")
    dash_added = _add_column_if_missing("os_types", "show_on_dashboard", "BOOLEAN DEFAULT 1")
    if order_added or dash_added:
        _backfill_os_types_ui_order()
    _ensure_os_types_ui_order_when_collapsed()
    _backfill_iso_was_extracted()
    # remote_chains table est créée via Base.metadata.create_all — pas besoin d'ALTER


def _backfill_os_types_ui_order() -> None:
    """Après ajout des colonnes UI : répartir l'ordre initial comme l'ancien tri par slug."""
    if "sqlite" not in settings.database_url:
        return
    try:
        from app.models.models import OsType
        from app.services.os_type_order import UI_OS_SLUG_ORDER

        rank = {s: i for i, s in enumerate(UI_OS_SLUG_ORDER)}
        db = SessionLocal()
        try:
            ots = list(db.query(OsType).all())
            if not ots:
                return
            known = [ot for ot in ots if ot.slug in rank]
            unknown = [ot for ot in ots if ot.slug not in rank]
            known.sort(key=lambda ot: rank[ot.slug])
            unknown.sort(key=lambda ot: (ot.label or ot.slug or "").lower())
            ordered = known + unknown
            for idx, ot in enumerate(ordered):
                ot.ui_sort_order = idx
                if getattr(ot, "show_on_dashboard", None) is None:
                    ot.show_on_dashboard = True
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("Migration : backfill os_types ui_sort_order")


def _ensure_os_types_ui_order_when_collapsed() -> None:
    """Installations toutes neuves : tous les ui_sort_order à 0 — réappliquer l'ordre par slug."""
    if "sqlite" not in settings.database_url:
        return
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT COUNT(*), COUNT(DISTINCT ui_sort_order) FROM os_types")
            ).fetchone()
        if not row or row[0] <= 1 or row[1] > 1:
            return
        _backfill_os_types_ui_order()
    except Exception:
        logger.exception("Migration : ensure os_types ui_sort_order")


def _backfill_iso_was_extracted() -> None:
    """Déjà déployés : ISO prête avec chemins boot — considéré comme déjà extrait au moins une fois."""
    if "sqlite" not in settings.database_url:
        return
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    """
                    UPDATE iso_versions
                    SET iso_was_extracted = 1
                    WHERE (iso_was_extracted = 0 OR iso_was_extracted IS NULL)
                      AND iso_path IS NOT NULL AND LENGTH(TRIM(iso_path)) > 0
                      AND status = 'ready'
                      AND id IN (
                        SELECT iso_version_id FROM boot_entries
                        WHERE COALESCE(kernel_path, '') <> ''
                           OR COALESCE(initrd_path, '') <> ''
                           OR COALESCE(boot_wim_path, '') <> ''
                           OR COALESCE(esxi_boot_cfg_path, '') <> ''
                           OR TRIM(COALESCE(esxi_modules, '')) <> ''
                           OR COALESCE(modloop_path, '') <> ''
                      )
                    """
                )
            )
            conn.commit()
    except Exception:
        logger.exception("Migration : backfill iso_was_extracted")


def _add_column_if_missing(table: str, column: str, col_type: str) -> bool:
    """Retourne True si la colonne a été créée (préremplissage possible)."""
    with engine.connect() as conn:
        try:
            # SQLite: check if column exists via PRAGMA
            result = conn.execute(text(f"PRAGMA table_info({table})"))
            columns = [row[1] for row in result.fetchall()]
            if column not in columns:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                conn.commit()
                logger.info("Migration : colonne '%s.%s' ajoutée", table, column)
                return True
            return False
        except Exception:
            logger.exception("Migration échouée pour %s.%s", table, column)
            return False
