"""
SQL schema definitions for ZebraFET.

PROJECT_DB_SCHEMA  — statements for per-project {name}.db
REGISTRY_DB_SCHEMA — statements for the global registry.db
"""

PROJECT_DB_SCHEMA: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version    INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS project (
        id                 INTEGER PRIMARY KEY CHECK (id = 1),
        project_name       TEXT NOT NULL,
        main_researcher    TEXT NOT NULL DEFAULT '',
        substance          TEXT NOT NULL DEFAULT '',
        concentration_unit TEXT NOT NULL DEFAULT 'mg/L',
        start_date         TEXT NOT NULL DEFAULT '',
        num_days           INTEGER NOT NULL DEFAULT 4,
        num_plates         INTEGER NOT NULL DEFAULT 1,
        plate_format       TEXT NOT NULL DEFAULT '96-well',
        report_notes       TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS substance_details (
        id                         INTEGER PRIMARY KEY CHECK (id = 1),
        cas_number                 TEXT NOT NULL DEFAULT '',
        molecular_weight           TEXT NOT NULL DEFAULT '',
        purity                     TEXT NOT NULL DEFAULT '',
        supplier                   TEXT NOT NULL DEFAULT '',
        physical_appearance        TEXT NOT NULL DEFAULT '',
        water_solubility           TEXT NOT NULL DEFAULT '',
        iupac_name                 TEXT NOT NULL DEFAULT '',
        solvent_used               TEXT NOT NULL DEFAULT '',
        positive_control_substance TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS test_conditions (
        id                   INTEGER PRIMARY KEY CHECK (id = 1),
        water_type           TEXT NOT NULL DEFAULT '',
        ph                   TEXT NOT NULL DEFAULT '',
        hardness             TEXT NOT NULL DEFAULT '',
        conductivity         TEXT NOT NULL DEFAULT '',
        photoperiod          TEXT NOT NULL DEFAULT '',
        temperature          TEXT NOT NULL DEFAULT '',
        dissolved_oxygen     TEXT NOT NULL DEFAULT '',
        acceptable_mortality REAL NOT NULL DEFAULT 10.0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS completed_days (
        day INTEGER PRIMARY KEY
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS concentration_groups (
        id         TEXT PRIMARY KEY,
        type       TEXT NOT NULL,
        value      REAL NOT NULL DEFAULT 0,
        replicates INTEGER NOT NULL DEFAULT 1,
        wells      INTEGER NOT NULL DEFAULT 4,
        per_plate  INTEGER NOT NULL DEFAULT 0,
        color      TEXT NOT NULL DEFAULT '#3498db',
        sort_order INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS concentration_settings (
        id               INTEGER PRIMARY KEY CHECK (id = 1),
        required_embryos INTEGER NOT NULL DEFAULT 20,
        required_plates  INTEGER NOT NULL DEFAULT 1
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS plate_layout (
        plate_index INTEGER NOT NULL,
        well_id     TEXT NOT NULL,
        conc_id     TEXT NOT NULL REFERENCES concentration_groups(id) ON DELETE CASCADE,
        PRIMARY KEY (plate_index, well_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS well_observations (
        day         INTEGER NOT NULL,
        plate_index INTEGER NOT NULL,
        well_id     TEXT NOT NULL,
        status      TEXT NOT NULL DEFAULT 'Live Embryo',
        notes       TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (day, plate_index, well_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS well_sublethal_conditions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        day         INTEGER NOT NULL,
        plate_index INTEGER NOT NULL,
        well_id     TEXT NOT NULL,
        condition   TEXT NOT NULL,
        FOREIGN KEY (day, plate_index, well_id)
            REFERENCES well_observations(day, plate_index, well_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS well_lethal_conditions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        day         INTEGER NOT NULL,
        plate_index INTEGER NOT NULL,
        well_id     TEXT NOT NULL,
        condition   TEXT NOT NULL,
        FOREIGN KEY (day, plate_index, well_id)
            REFERENCES well_observations(day, plate_index, well_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS well_photos (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        day           INTEGER NOT NULL,
        plate_index   INTEGER NOT NULL,
        well_id       TEXT NOT NULL,
        relative_path TEXT NOT NULL UNIQUE,
        added_at      TEXT NOT NULL,
        FOREIGN KEY (day, plate_index, well_id)
            REFERENCES well_observations(day, plate_index, well_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_well_obs_day            ON well_observations(day)",
    "CREATE INDEX IF NOT EXISTS idx_well_obs_composite      ON well_observations(day, plate_index, well_id)",
    "CREATE INDEX IF NOT EXISTS idx_plate_layout_plate      ON plate_layout(plate_index)",
    "CREATE INDEX IF NOT EXISTS idx_photos_well             ON well_photos(day, plate_index, well_id)",
    "CREATE INDEX IF NOT EXISTS idx_sublethal_composite     ON well_sublethal_conditions(day, plate_index, well_id)",
    "CREATE INDEX IF NOT EXISTS idx_lethal_composite        ON well_lethal_conditions(day, plate_index, well_id)",
    """
    CREATE TABLE IF NOT EXISTS test_organisms (
        id                INTEGER PRIMARY KEY CHECK (id = 1),
        strain            TEXT NOT NULL DEFAULT '',
        source            TEXT NOT NULL DEFAULT '',
        collection_method TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS methodology (
        id                   INTEGER PRIMARY KEY CHECK (id = 1),
        test_procedure       TEXT NOT NULL DEFAULT 'Static',
        solution_preparation TEXT NOT NULL DEFAULT '',
        selection_criteria   TEXT NOT NULL DEFAULT ''
    )
    """,
]

REGISTRY_DB_SCHEMA: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS projects (
        project_name         TEXT PRIMARY KEY,
        db_path              TEXT NOT NULL UNIQUE,
        main_researcher      TEXT NOT NULL DEFAULT '',
        substance            TEXT NOT NULL DEFAULT '',
        num_days             INTEGER NOT NULL DEFAULT 4,
        completed_days_count INTEGER NOT NULL DEFAULT 0,
        start_date           TEXT NOT NULL DEFAULT '',
        last_modified        TEXT NOT NULL
    )
    """,
]

CURRENT_SCHEMA_VERSION = 6


def initialize_project_db(conn) -> None:
    """Create all project tables and set schema version if new database."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA cache_size=-16384")
    conn.execute("PRAGMA temp_store=MEMORY")
    with conn:
        for stmt in PROJECT_DB_SCHEMA:
            conn.execute(stmt)
        row = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()
        if row[0] == 0:
            import datetime
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (CURRENT_SCHEMA_VERSION, datetime.datetime.now(datetime.timezone.utc).isoformat()),
            )


def initialize_registry_db(conn) -> None:
    """Create the global registry table."""
    with conn:
        for stmt in REGISTRY_DB_SCHEMA:
            conn.execute(stmt)
