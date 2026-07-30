"""
Schema migration runner for per-project databases.

Each migration is a callable that receives a sqlite3.Connection and applies
the required schema changes. Migrations are keyed by their target version
number and run in order when the stored version is below the current version.
"""
import datetime
import logging
import sqlite3
from typing import Callable

from src.database.schema import CURRENT_SCHEMA_VERSION

log = logging.getLogger(__name__)

def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Add temperature, dissolved_oxygen, and acceptable_mortality to test_conditions."""
    for stmt in (
        "ALTER TABLE test_conditions ADD COLUMN temperature TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE test_conditions ADD COLUMN dissolved_oxygen TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE test_conditions ADD COLUMN acceptable_mortality REAL NOT NULL DEFAULT 10.0",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            # Column already exists (safe to ignore)
            if "duplicate column" not in str(e).lower():
                raise


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Add plate_format column to project table."""
    try:
        conn.execute(
            "ALTER TABLE project ADD COLUMN "
            "plate_format TEXT NOT NULL DEFAULT '96-well'"
        )
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    """Rebuild plate_layout to add ON DELETE CASCADE on the conc_id FK.

    SQLite does not support ALTER TABLE ADD CONSTRAINT, so the table must be
    recreated using the standard rename-copy-drop pattern.
    """
    conn.execute("""
        CREATE TABLE plate_layout_new (
            plate_index INTEGER NOT NULL,
            well_id     TEXT NOT NULL,
            conc_id     TEXT NOT NULL REFERENCES concentration_groups(id) ON DELETE CASCADE,
            PRIMARY KEY (plate_index, well_id)
        )
    """)
    conn.execute("INSERT INTO plate_layout_new SELECT * FROM plate_layout")
    conn.execute("DROP TABLE plate_layout")
    conn.execute("ALTER TABLE plate_layout_new RENAME TO plate_layout")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plate_layout_plate ON plate_layout(plate_index)"
    )


def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    """Add test_organisms and methodology singleton tables."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS test_organisms (
            id                INTEGER PRIMARY KEY CHECK (id = 1),
            strain            TEXT NOT NULL DEFAULT '',
            source            TEXT NOT NULL DEFAULT '',
            collection_method TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS methodology (
            id                   INTEGER PRIMARY KEY CHECK (id = 1),
            test_procedure       TEXT NOT NULL DEFAULT 'Static',
            solution_preparation TEXT NOT NULL DEFAULT '',
            selection_criteria   TEXT NOT NULL DEFAULT ''
        )
    """)


def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    """Add composite indexes on well_observations, well_sublethal_conditions, and well_lethal_conditions."""
    for stmt in (
        "CREATE INDEX IF NOT EXISTS idx_well_obs_composite  ON well_observations(day, plate_index, well_id)",
        "CREATE INDEX IF NOT EXISTS idx_sublethal_composite ON well_sublethal_conditions(day, plate_index, well_id)",
        "CREATE INDEX IF NOT EXISTS idx_lethal_composite    ON well_lethal_conditions(day, plate_index, well_id)",
    ):
        conn.execute(stmt)


def _migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    """Add auto_filled flag to well_observations to distinguish user-entered from derived rows."""
    try:
        conn.execute(
            "ALTER TABLE well_observations ADD COLUMN auto_filled INTEGER NOT NULL DEFAULT 0"
        )
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise


def _migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    """Add the per-day water_quality_log table (OECD TG 236 in-test monitoring)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS water_quality_log (
            day              INTEGER PRIMARY KEY,
            temperature      TEXT NOT NULL DEFAULT '',
            dissolved_oxygen TEXT NOT NULL DEFAULT '',
            ph               TEXT NOT NULL DEFAULT '',
            conductivity     TEXT NOT NULL DEFAULT '',
            notes            TEXT NOT NULL DEFAULT ''
        )
        """
    )


def _migrate_v8_to_v9(conn: sqlite3.Connection) -> None:
    """Add the analysis_settings table so curve-fitting choices persist.

    Model, Abbott's correction and the reference-control selection materially
    change the reported LC50 and NOEC, so re-deriving them from UI defaults on
    every launch made a saved project's analysis irreproducible.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_settings (
            id              INTEGER PRIMARY KEY CHECK (id = 1),
            model_mode      TEXT NOT NULL DEFAULT 'LL4',
            bottom          REAL NOT NULL DEFAULT 0.0,
            top             REAL NOT NULL DEFAULT 100.0,
            abbott          INTEGER NOT NULL DEFAULT 0,
            control_mode    TEXT NOT NULL DEFAULT 'pooled'
        )
        """
    )


def _migrate_v9_to_v10(conn: sqlite3.Connection) -> None:
    """Rewrite well_photos.relative_path to use forward slashes.

    Photos added on Windows were stored with backslashes because the path came
    from os.path.join, while every lookup normalized to forward slashes. The
    two never matched, so removing such a photo silently did nothing and the
    same project opened on macOS or Linux could not resolve its own images.
    """
    # REPLACE leaves rows without a backslash untouched, so no WHERE clause is
    # needed and the migration is safe to re-run.
    conn.execute(r"UPDATE well_photos SET relative_path = REPLACE(relative_path, '\', '/')")


def _migrate_v10_to_v11(conn: sqlite3.Connection) -> None:
    """Record which multiplicity correction the NOEC/LOEC was derived under.

    Holm and Bonferroni can disagree about the LOEC, so which was used is part of
    the result rather than a preference.
    """
    try:
        conn.execute(
            "ALTER TABLE analysis_settings ADD COLUMN "
            "noec_correction TEXT NOT NULL DEFAULT 'holm'"
        )
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise


def _migrate_v11_to_v12(conn: sqlite3.Connection) -> None:
    """Record the batch fertilization rate and the test species.

    OECD TG 236 lists both among the contents a test report must carry, and the
    fertilization rate is a validity criterion in its own right (§9a, ≥70%).
    Neither had anywhere to live: the species was a fixed label in the settings
    dialog that never reached the database.

    The fertilization rate is TEXT rather than REAL so that "not recorded" stays
    distinguishable from a recorded 0%; the report omits the criterion entirely
    when the field is blank.
    """
    for stmt in (
        "ALTER TABLE test_conditions ADD COLUMN "
        "fertilization_rate TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE test_organisms ADD COLUMN "
        "species TEXT NOT NULL DEFAULT 'Danio rerio'",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise


def _migrate_v12_to_v13(conn: sqlite3.Connection) -> None:
    """Rename the sublethal endpoint 'Tail / fin malformation' to 'Tail malformation'.

    The vocabulary carried two overlapping entries — 'Tail / fin malformation'
    and 'Fin malformation or absence' — so the same observation could be filed
    under either, which is the scoring inconsistency the structured endpoint list
    exists to prevent. The tail entry is narrowed and the fin entry left as the
    single home for fin findings.

    The mapping is not perfectly recoverable: a row recorded under the old label
    may have meant a tail finding, a fin finding, or both. It maps to
    'Tail malformation' because that is what the old label led with, and so is
    the likeliest reading of what the scorer selected.
    """
    conn.execute(
        "UPDATE well_sublethal_conditions SET condition = ? WHERE condition = ?",
        ("Tail malformation", "Tail / fin malformation"),
    )


# Map target_version → migration function
# Add new entries here as the schema evolves.
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    # Version 1 is the initial schema — created by initialize_project_db().
    2: _migrate_v1_to_v2,
    3: _migrate_v2_to_v3,
    4: _migrate_v3_to_v4,
    5: _migrate_v4_to_v5,
    6: _migrate_v5_to_v6,
    7: _migrate_v6_to_v7,
    8: _migrate_v7_to_v8,
    9: _migrate_v8_to_v9,
    10: _migrate_v9_to_v10,
    11: _migrate_v10_to_v11,
    12: _migrate_v11_to_v12,
    13: _migrate_v12_to_v13,
}


class MigrationRunner:
    """
    Applies any pending schema migrations to a project database connection.

    Usage:
        conn = sqlite3.connect(db_path)
        MigrationRunner(conn).run()
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def run(self) -> None:
        """Check the stored schema version and apply any pending migrations."""
        try:
            row = self._conn.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            ).fetchone()
            current = row[0] if row else 0
        except sqlite3.OperationalError:
            # schema_version table doesn't exist yet (brand-new DB before first init)
            current = 0

        if current >= CURRENT_SCHEMA_VERSION:
            return

        for version in range(current + 1, CURRENT_SCHEMA_VERSION + 1):
            if version in MIGRATIONS:
                log.info(f"Applying DB migration to version {version}")
                try:
                    with self._conn:
                        MIGRATIONS[version](self._conn)
                        self._conn.execute(
                            "UPDATE schema_version SET version=?, applied_at=?",
                            (version, datetime.datetime.now(datetime.timezone.utc).isoformat()),
                        )
                    log.info(f"Migration to version {version} succeeded.")
                except Exception as e:
                    log.error(f"Migration to version {version} failed: {e}", exc_info=True)
                    raise
