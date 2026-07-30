import datetime
import logging
import os
import random
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from src.database.schema import initialize_project_db
from src.database.migrations import MigrationRunner
from src.core.constants import (
    LIVE_STATUSES as _LIVE_STATUSES,
    DEAD_STATUSES as _DEAD_STATUSES,
    STATUS_ABSENT,
)

log = logging.getLogger(__name__)

# Subdirectory names inside each project folder
PHOTOS_SUBDIR = "photos"
REPORTS_SUBDIR = "reports"


def normalize_rel_path(path: str) -> str:
    """Canonical form of a project-relative path: forward slashes throughout.

    well_photos.relative_path is matched by equality, so the separator has to be
    identical at write and at lookup. os.path.join yields backslashes on Windows
    while lookups normalized to forward slashes, which meant a photo added on
    Windows could never be found again to remove it. Projects are also exchanged
    across platforms via .zfet archives, so the stored form has to be one both
    can produce.
    """
    return path.replace("\\", "/")


class ProjectManager:
    """
    Manages all data operations for a single FET project backed by a SQLite
    database ({project_name}.db) inside the project directory.

    All writes are transactional and take effect immediately — there is no
    manual save step or dirty flag.  The registry.db is updated automatically
    after writes that change project metadata.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, project_dir_path: str) -> None:
        if not project_dir_path:
            raise ValueError("Project directory path cannot be empty.")

        self.project_dir = project_dir_path
        self._project_name = os.path.basename(project_dir_path)
        self.db_path = os.path.join(project_dir_path, f"{self._project_name}.db")

        # One connection per thread. A single shared connection carries a single
        # transaction, so a read issued from the thread pool while the GUI thread
        # sat inside `with self._conn:` observed that transaction's uncommitted
        # state, and two threads opening transactions at once would have had the
        # inner commit end the outer one. Separate connections give each thread
        # its own transaction scope; SQLite serializes them at the file level.
        self._local = threading.local()
        self._connections: List[sqlite3.Connection] = []
        self._connections_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._conc_map_cache: Optional[Dict[str, Any]] = None
        initialize_project_db(self._conn)
        MigrationRunner(self._conn).run()
        self._ensure_subdirs()
        self._backfill_legacy_finalized_days()

        log.info(f"ProjectManager ready for '{self._project_name}' at {self.db_path}")

    @classmethod
    def create_new(cls, project_dir_path: str, initial_data: dict) -> "ProjectManager":
        """
        Create a brand-new project directory, write initial_data to the DB,
        and return a ready-to-use ProjectManager.
        """
        os.makedirs(project_dir_path, exist_ok=True)
        manager = cls(project_dir_path)
        manager._write_initial_data(initial_data)
        manager._sync_to_registry()
        log.info(f"New project '{manager._project_name}' created at {project_dir_path}")
        return manager

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    def _open_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA cache_size=-16384")
        conn.execute("PRAGMA temp_store=MEMORY")
        return conn

    @property
    def _conn(self) -> sqlite3.Connection:
        """This thread's connection, opened on first use."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._open_connection()
            self._local.conn = conn
            with self._connections_lock:
                self._connections.append(conn)
        return conn

    def _ensure_subdirs(self) -> None:
        os.makedirs(os.path.join(self.project_dir, PHOTOS_SUBDIR), exist_ok=True)
        os.makedirs(os.path.join(self.project_dir, REPORTS_SUBDIR), exist_ok=True)

    def checkpoint(self):
        """Fold the write-ahead log back into the .db file.

        WAL mode keeps recent commits in a sidecar until SQLite decides to fold
        them in, so a project folder copied mid-session can be missing its most
        recent observations. Used before export and by the explicit save action.

        Returns the PRAGMA's result row, whose first element is 0 when the
        checkpoint completed; callers that are about to copy the file check it.
        """
        return self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()

    def close(self) -> None:
        """Close every connection this project opened, across all threads."""
        with self._connections_lock:
            connections, self._connections = self._connections, []
        for conn in connections:
            try:
                conn.close()
            except sqlite3.ProgrammingError:
                pass  # Already closed
            except Exception as e:
                log.warning(f"Error closing database connection: {e}")
        self._local = threading.local()

    def _transact(self, fn, *args, **kwargs):
        """
        Execute fn(*args, **kwargs) inside a transaction with exponential back-off
        retry on OperationalError (database locked) — up to 8 attempts, 50 ms
        initial delay, doubling with uniform jitter.  busy_timeout=30000 serves
        as the last-resort SQLite-level safeguard.
        """
        delay = 0.050
        for attempt in range(8):
            try:
                with self._conn:
                    return fn(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower() or attempt == 7:
                    raise
                jitter = random.uniform(0, delay * 0.2)
                time.sleep(delay + jitter)
                delay *= 2

    # ------------------------------------------------------------------
    # Project metadata
    # ------------------------------------------------------------------

    def get_project_name(self) -> str:
        row = self._conn.execute(
            "SELECT project_name FROM project WHERE id = 1"
        ).fetchone()
        return row["project_name"] if row else self._project_name

    def get_project_directory(self) -> str:
        return self.project_dir

    def get_project_info(self) -> Dict[str, Any]:
        """Return all top-level project fields as a dict."""
        row = self._conn.execute("SELECT * FROM project WHERE id = 1").fetchone()
        if not row:
            return {}
        info = dict(row)
        info["completed_days"] = self.get_completed_days()
        return info

    def get_plate_format(self) -> str:
        row = self._conn.execute(
            "SELECT plate_format FROM project WHERE id = 1"
        ).fetchone()
        return row[0] if row else "96-well"

    def get_plate_dimensions(self) -> tuple:
        """Return (rows, cols) for the project's plate format."""
        from src.core.constants import PLATE_FORMATS, DEFAULT_PLATE_FORMAT
        fmt = self.get_plate_format()
        return PLATE_FORMATS.get(fmt, PLATE_FORMATS[DEFAULT_PLATE_FORMAT])

    def clear_plate_layout(self) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM plate_layout")

    def update_project_info(self, **fields) -> None:
        allowed = {
            "project_name", "main_researcher", "substance",
            "concentration_unit", "start_date", "num_days",
            "num_plates", "plate_format", "report_notes",
        }
        to_set = {k: v for k, v in fields.items() if k in allowed}
        if not to_set:
            return
        cols = ", ".join(f"{k} = ?" for k in to_set)
        with self._conn:
            self._conn.execute(
                f"UPDATE project SET {cols} WHERE id = 1",
                list(to_set.values()),
            )
        self._sync_to_registry()

    def get_substance_details(self) -> Dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM substance_details WHERE id = 1"
        ).fetchone()
        return dict(row) if row else {}

    def update_substance_details(self, **fields) -> None:
        allowed = {
            "cas_number", "molecular_weight", "purity", "supplier",
            "physical_appearance", "water_solubility", "iupac_name",
            "solvent_used", "positive_control_substance",
        }
        to_set = {k: v for k, v in fields.items() if k in allowed}
        if not to_set:
            return
        cols = ", ".join(f"{k} = ?" for k in to_set)
        with self._conn:
            self._conn.execute(
                f"UPDATE substance_details SET {cols} WHERE id = 1",
                list(to_set.values()),
            )

    def get_test_conditions(self) -> Dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM test_conditions WHERE id = 1"
        ).fetchone()
        return dict(row) if row else {}

    def update_test_conditions(self, **fields) -> None:
        allowed = {
            "water_type", "ph", "hardness", "conductivity", "photoperiod",
            "temperature", "dissolved_oxygen", "acceptable_mortality",
            "fertilization_rate",
        }
        to_set = {k: v for k, v in fields.items() if k in allowed}
        # The row is created even for an empty payload: project creation passes
        # whatever the wizard collected, which may be nothing, and returning early
        # left the singleton missing so the getter reported {} instead of the
        # schema defaults.
        with self._conn:
            self._conn.execute("INSERT OR IGNORE INTO test_conditions (id) VALUES (1)")
            if to_set:
                cols = ", ".join(f"{k} = ?" for k in to_set)
                self._conn.execute(
                    f"UPDATE test_conditions SET {cols} WHERE id = 1",
                    list(to_set.values()),
                )

    def get_water_quality_log(self) -> Dict[int, Dict[str, Any]]:
        """Return the per-day water-quality log as {day: {temperature, ...}}."""
        rows = self._conn.execute(
            "SELECT * FROM water_quality_log ORDER BY day"
        ).fetchall()
        return {int(r["day"]): dict(r) for r in rows}

    def save_water_quality(self, day: int, temperature: str = "", dissolved_oxygen: str = "",
                           ph: str = "", conductivity: str = "", notes: str = "") -> None:
        """Upsert the water-quality measurements recorded on *day*."""
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO water_quality_log (day, temperature, dissolved_oxygen, ph, conductivity, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(day) DO UPDATE SET
                    temperature=excluded.temperature,
                    dissolved_oxygen=excluded.dissolved_oxygen,
                    ph=excluded.ph,
                    conductivity=excluded.conductivity,
                    notes=excluded.notes
                """,
                (day, temperature, dissolved_oxygen, ph, conductivity, notes),
            )

    def delete_water_quality(self, day: int) -> None:
        """Remove the water-quality row for *day*, if one exists.

        Used when every field has been cleared. An all-blank row is not a
        measurement: leaving it stored would put an empty line in the report's
        monitoring table and make the log appear populated.
        """
        with self._conn:
            self._conn.execute("DELETE FROM water_quality_log WHERE day = ?", (day,))

    def get_test_organisms(self) -> Dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM test_organisms WHERE id = 1"
        ).fetchone()
        return dict(row) if row else {}

    def update_test_organisms(self, **fields) -> None:
        allowed = {"species", "strain", "source", "collection_method"}
        to_set = {k: v for k, v in fields.items() if k in allowed}
        with self._conn:
            self._conn.execute("INSERT OR IGNORE INTO test_organisms (id) VALUES (1)")
            if to_set:
                cols = ", ".join(f"{k} = ?" for k in to_set)
                self._conn.execute(
                    f"UPDATE test_organisms SET {cols} WHERE id = 1",
                    list(to_set.values()),
                )

    def get_methodology(self) -> Dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM methodology WHERE id = 1"
        ).fetchone()
        return dict(row) if row else {}

    def update_methodology(self, **fields) -> None:
        allowed = {"test_procedure", "solution_preparation", "selection_criteria"}
        to_set = {k: v for k, v in fields.items() if k in allowed}
        with self._conn:
            self._conn.execute("INSERT OR IGNORE INTO methodology (id) VALUES (1)")
            if to_set:
                cols = ", ".join(f"{k} = ?" for k in to_set)
                self._conn.execute(
                    f"UPDATE methodology SET {cols} WHERE id = 1",
                    list(to_set.values()),
                )

    # ------------------------------------------------------------------
    # Analysis settings
    # ------------------------------------------------------------------

    #: Columns the UI is allowed to persist, with the defaults applied when a
    #: project has never had its analysis settings saved.
    ANALYSIS_SETTING_DEFAULTS: Dict[str, Any] = {
        "model_mode": "LL4",
        "bottom": 0.0,
        "top": 100.0,
        "abbott": 0,
        "control_mode": "pooled",
        "noec_correction": "holm",
    }

    def get_analysis_settings(self) -> Dict[str, Any]:
        """Persisted curve-fitting and reference-control choices for this project.

        Returns the defaults when the project has never saved any, so callers
        never have to distinguish "unset" from "explicitly default".
        """
        row = self._conn.execute(
            "SELECT * FROM analysis_settings WHERE id = 1"
        ).fetchone()
        settings = dict(self.ANALYSIS_SETTING_DEFAULTS)
        if row:
            stored = dict(row)
            stored.pop("id", None)
            settings.update({k: v for k, v in stored.items() if v is not None})
        settings["abbott"] = bool(settings["abbott"])
        return settings

    def save_analysis_settings(self, **fields) -> None:
        """Upsert the analysis settings. Unknown keys are ignored."""
        to_set = {k: v for k, v in fields.items()
                  if k in self.ANALYSIS_SETTING_DEFAULTS}
        if not to_set:
            return
        if "abbott" in to_set:
            to_set["abbott"] = int(bool(to_set["abbott"]))
        cols = ", ".join(f"{k} = ?" for k in to_set)
        with self._conn:
            self._conn.execute("INSERT OR IGNORE INTO analysis_settings (id) VALUES (1)")
            self._conn.execute(
                f"UPDATE analysis_settings SET {cols} WHERE id = 1",
                list(to_set.values()),
            )

    # ------------------------------------------------------------------
    # Concentrations
    # ------------------------------------------------------------------

    def get_concentrations(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM concentration_groups ORDER BY sort_order"
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["per_plate"] = bool(d["per_plate"])
            result.append(d)
        return result

    def get_concentration_map(self) -> Dict[str, Dict[str, Any]]:
        # Read from any thread, so the fill is guarded; the query runs outside the
        # lock because it goes to this thread's own connection.
        cached = self._conc_map_cache
        if cached is not None:
            return cached
        built = {c["id"]: c for c in self.get_concentrations()}
        with self._cache_lock:
            if self._conc_map_cache is None:
                self._conc_map_cache = built
            return self._conc_map_cache

    def set_concentrations(
        self,
        concentrations: List[Dict[str, Any]],
        required_embryos: int,
        required_plates: int,
    ) -> None:
        """Replace all concentration groups and update the settings row."""
        self._conc_map_cache = None
        new_ids = {c["id"] for c in concentrations}
        with self._conn:
            for i, c in enumerate(concentrations):
                self._conn.execute(
                    """
                    INSERT INTO concentration_groups
                        (id, type, value, replicates, wells, per_plate, color, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        type       = excluded.type,
                        value      = excluded.value,
                        replicates = excluded.replicates,
                        wells      = excluded.wells,
                        per_plate  = excluded.per_plate,
                        color      = excluded.color,
                        sort_order = excluded.sort_order
                    """,
                    (
                        c["id"], c["type"], c.get("value", 0),
                        c.get("replicates", 1), c.get("wells", 4),
                        1 if c.get("per_plate", False) else 0,
                        c.get("color", "#3498db"), i,
                    ),
                )
            if new_ids:
                placeholders = ",".join("?" * len(new_ids))
                self._conn.execute(
                    f"DELETE FROM concentration_groups WHERE id NOT IN ({placeholders})",
                    list(new_ids),
                )
            else:
                self._conn.execute("DELETE FROM concentration_groups")
            self._conn.execute(
                """
                INSERT INTO concentration_settings (id, required_embryos, required_plates)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    required_embryos = excluded.required_embryos,
                    required_plates  = excluded.required_plates
                """,
                (required_embryos, required_plates),
            )

    def get_concentration_settings(self) -> Dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM concentration_settings WHERE id = 1"
        ).fetchone()
        return dict(row) if row else {"required_embryos": 0, "required_plates": 1}

    # ------------------------------------------------------------------
    # Plate layout
    # ------------------------------------------------------------------

    def get_plate_layout(self, plate_index: int) -> Dict[str, str]:
        rows = self._conn.execute(
            "SELECT well_id, conc_id FROM plate_layout WHERE plate_index = ?",
            (plate_index,),
        ).fetchall()
        return {r["well_id"]: r["conc_id"] for r in rows}

    def get_all_plate_layouts(self) -> Dict[str, Dict[str, str]]:
        rows = self._conn.execute(
            "SELECT plate_index, well_id, conc_id FROM plate_layout"
        ).fetchall()
        layouts: Dict[str, Dict[str, str]] = {}
        for r in rows:
            layouts.setdefault(str(r["plate_index"]), {})[r["well_id"]] = r["conc_id"]
        return layouts

    def commit_plate_layout(self, new_layout_data: Dict[str, Dict[str, str]]) -> None:
        """Replace the entire plate layout atomically."""
        with self._conn:
            self._conn.execute("DELETE FROM plate_layout")
            for plate_str, wells in new_layout_data.items():
                plate_idx = int(plate_str)
                for well_id, conc_id in wells.items():
                    self._conn.execute(
                        "INSERT INTO plate_layout (plate_index, well_id, conc_id) VALUES (?, ?, ?)",
                        (plate_idx, well_id, conc_id),
                    )
        log.info("Plate layout committed to DB.")

    def get_assignment_counters(self) -> Dict[str, Dict[str, int]]:
        """
        Returns planned vs. assigned well counts per concentration group.
        Used by the plate layout UI to show progress.
        """
        info = self.get_project_info()
        num_plates = info.get("num_plates", 1)
        concentrations = self.get_concentrations()

        # Count assigned wells per conc_id
        rows = self._conn.execute(
            "SELECT conc_id, COUNT(*) AS cnt FROM plate_layout GROUP BY conc_id"
        ).fetchall()
        assigned = {r["conc_id"]: r["cnt"] for r in rows}

        counters: Dict[str, Dict[str, int]] = {}
        for conc in concentrations:
            cid = conc["id"]
            if conc["per_plate"]:
                planned = conc["wells"] * num_plates
            else:
                planned = conc["replicates"] * conc["wells"]
            counters[cid] = {"planned": planned, "assigned": assigned.get(cid, 0)}
        return counters

    # ------------------------------------------------------------------
    # Days
    # ------------------------------------------------------------------

    def get_completed_days(self) -> List[int]:
        rows = self._conn.execute(
            "SELECT day FROM completed_days ORDER BY day"
        ).fetchall()
        return [r["day"] for r in rows]

    def set_day_as_completed(self, day: int) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO completed_days (day) VALUES (?)", (day,)
            )
        self._sync_to_registry()
        log.info(f"Day {day} marked as completed.")

    def set_day_as_incomplete(self, day: int) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM completed_days WHERE day = ?", (day,)
            )
        self._sync_to_registry()
        log.info(f"Day {day} re-opened for editing.")

    def get_first_incomplete_day(self) -> Optional[int]:
        info = self.get_project_info()
        num_days = info.get("num_days", 1)
        completed = set(self.get_completed_days())
        for day in range(1, num_days + 1):
            if day not in completed:
                return day
        return None

    # ------------------------------------------------------------------
    # Well observations
    # ------------------------------------------------------------------

    def get_well_data(self, day: int, plate: int, well_id: str) -> Dict[str, Any]:
        """Return the observation dict for a single well, or {} if not yet recorded."""
        obs = self._conn.execute(
            "SELECT status, notes, auto_filled FROM well_observations WHERE day=? AND plate_index=? AND well_id=?",
            (day, plate, well_id),
        ).fetchone()
        if not obs:
            return {}

        sublethal = [
            r["condition"] for r in self._conn.execute(
                "SELECT condition FROM well_sublethal_conditions WHERE day=? AND plate_index=? AND well_id=?",
                (day, plate, well_id),
            ).fetchall()
        ]
        lethal = [
            r["condition"] for r in self._conn.execute(
                "SELECT condition FROM well_lethal_conditions WHERE day=? AND plate_index=? AND well_id=?",
                (day, plate, well_id),
            ).fetchall()
        ]
        photos = [
            r["relative_path"] for r in self._conn.execute(
                "SELECT relative_path FROM well_photos WHERE day=? AND plate_index=? AND well_id=? ORDER BY added_at",
                (day, plate, well_id),
            ).fetchall()
        ]
        return {
            "status": obs["status"],
            "notes": obs["notes"],
            "auto_filled": obs["auto_filled"],
            "sublethal_conditions": sublethal,
            "lethal_conditions": lethal,
            "photos": photos,
        }

    def save_well_data(
        self,
        day: int,
        plate_index: int,
        well_id: str,
        status: str,
        sublethal_conditions: List[str],
        lethal_conditions: List[str],
        notes: str,
        auto_filled: int = 0,
    ) -> None:
        """Persist a well observation (INSERT OR REPLACE) in a single transaction."""
        def _write():
            self._conn.execute(
                """
                INSERT INTO well_observations (day, plate_index, well_id, status, notes, auto_filled)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(day, plate_index, well_id) DO UPDATE SET
                    status      = excluded.status,
                    notes       = excluded.notes,
                    auto_filled = excluded.auto_filled
                """,
                (day, plate_index, well_id, status, notes, auto_filled),
            )
            self._conn.execute(
                "DELETE FROM well_sublethal_conditions WHERE day=? AND plate_index=? AND well_id=?",
                (day, plate_index, well_id),
            )
            self._conn.execute(
                "DELETE FROM well_lethal_conditions WHERE day=? AND plate_index=? AND well_id=?",
                (day, plate_index, well_id),
            )
            for cond in sublethal_conditions:
                self._conn.execute(
                    "INSERT INTO well_sublethal_conditions (day, plate_index, well_id, condition) VALUES (?, ?, ?, ?)",
                    (day, plate_index, well_id, cond),
                )
            for cond in lethal_conditions:
                self._conn.execute(
                    "INSERT INTO well_lethal_conditions (day, plate_index, well_id, condition) VALUES (?, ?, ?, ?)",
                    (day, plate_index, well_id, cond),
                )
        self._transact(_write)

    # ------------------------------------------------------------------
    # Results aggregation
    # ------------------------------------------------------------------

    def get_results_data(self, day: int) -> Dict[str, Any]:
        """
        Aggregate well data for a specific day to calculate per-concentration
        statistics (live, dead, malformed, total).
        """
        conc_map = self.get_concentration_map()
        results: Dict[str, Any] = {
            cid: {
                "live": 0, "dead": 0, "malformed": 0, "total": 0,
                "name": c.get("id", cid),
                "value": c.get("value", 0),
            }
            for cid, c in conc_map.items()
        }

        rows = self._conn.execute(
            """
            SELECT wo.well_id, wo.plate_index, wo.status, pl.conc_id,
                   COUNT(wsc.rowid) AS sublethal_count
            FROM well_observations wo
            JOIN plate_layout pl ON pl.plate_index = wo.plate_index AND pl.well_id = wo.well_id
            LEFT JOIN well_sublethal_conditions wsc
                ON wsc.day = wo.day AND wsc.plate_index = wo.plate_index AND wsc.well_id = wo.well_id
            WHERE wo.day = ?
            GROUP BY wo.well_id, wo.plate_index, wo.status, pl.conc_id
            """,
            (day,),
        ).fetchall()

        # Impute "Absent (use majority)" wells to the majority status of their
        # concentration group (same rule the analysis pipeline applies via
        # results_analysis_widget), so absent wells are counted identically here.
        import pandas as pd
        from src.core.biostatistics import impute_absent_as_majority

        records = [
            {
                "day": day,
                "conc_id": r["conc_id"],
                "status": r["status"],
                "sublethal_count": r["sublethal_count"],
            }
            for r in rows
            if r["conc_id"] in results
        ]
        if not records:
            return results

        df = pd.DataFrame(records)
        # Two-column group_cols mirrors the production call site so the groupby
        # key shape matches the function's internal tuple() lookup.
        df = impute_absent_as_majority(df, STATUS_ABSENT, group_cols=("day", "conc_id"))

        for row in df.itertuples(index=False):
            bucket = results[row.conc_id]
            bucket["total"] += 1
            if row.status in _LIVE_STATUSES:
                bucket["live"] += 1
            elif row.status in _DEAD_STATUSES:
                bucket["dead"] += 1
            # An all-absent group has no majority, so absent stays absent and
            # contributes to total only.
            if row.sublethal_count > 0:
                bucket["malformed"] += 1

        return results

    # ------------------------------------------------------------------
    # Photos
    # ------------------------------------------------------------------

    def add_photo_to_well(
        self, day: int, plate_index: int, well_id: str, source_photo_path: str
    ) -> Optional[str]:
        if not source_photo_path or not os.path.isfile(source_photo_path):
            log.error(f"Photo add failed: source not found at '{source_photo_path}'")
            return None
        from PIL import Image, UnidentifiedImageError
        try:
            # Ensure the observation row exists so the FK won't fail.
            # Uses carried-forward logic instead of hardcoding Live Embryo.
            self._ensure_observation_row(day, plate_index, well_id)

            day_photo_dir = os.path.join(self.project_dir, PHOTOS_SUBDIR, f"Day_{day}")
            os.makedirs(day_photo_dir, exist_ok=True)

            unique_filename = f"{well_id}_Plate{plate_index}_{uuid.uuid4().hex[:8]}.jpg"
            destination_path = os.path.join(day_photo_dir, unique_filename)

            with Image.open(source_photo_path) as img:
                rgb_img = img.convert("RGB")
                rgb_img.save(destination_path, "jpeg", quality=95)

            relative_path = normalize_rel_path(
                os.path.join(PHOTOS_SUBDIR, f"Day_{day}", unique_filename)
            )
            added_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO well_photos (day, plate_index, well_id, relative_path, added_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (day, plate_index, well_id, relative_path, added_at),
                )

            log.info(f"Photo added: {relative_path}")
            return relative_path

        except UnidentifiedImageError:
            log.error(f"Photo add failed: '{source_photo_path}' is not a valid image.")
            return None
        except Exception as e:
            log.error(f"Photo add failed: {e}", exc_info=True)
            return None

    def remove_photo_by_path(self, full_photo_path: str) -> bool:
        try:
            rel = normalize_rel_path(
                os.path.relpath(
                    os.path.normpath(full_photo_path),
                    os.path.normpath(self.project_dir),
                )
            )
            with self._conn:
                # Normalizing the stored value inside the comparison matches rows
                # in either separator form, so a row that predates the v10
                # migration is still removable rather than stranded in the table.
                cur = self._conn.execute(
                    r"DELETE FROM well_photos WHERE REPLACE(relative_path, '\', '/') = ?",
                    (rel,),
                )
            if cur.rowcount > 0:
                log.info(f"Photo reference removed: {rel}")
                try:
                    full_path = self.get_full_photo_path(rel)
                    if os.path.isfile(full_path):
                        os.remove(full_path)
                except OSError as e:
                    log.warning(f"Could not delete photo file '{rel}': {e}")
                return True
            log.warning(f"Photo reference not found for removal: {rel}")
            return False
        except Exception as e:
            log.error(f"Error removing photo reference: {e}", exc_info=True)
            return False

    def get_photos_for_well(self, day: int, plate_index: int, well_id: str) -> List[str]:
        rows = self._conn.execute(
            "SELECT relative_path FROM well_photos WHERE day=? AND plate_index=? AND well_id=? ORDER BY added_at",
            (day, plate_index, well_id),
        ).fetchall()
        return [r["relative_path"] for r in rows]

    def get_all_photos_with_metadata(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT day, plate_index, well_id, relative_path FROM well_photos ORDER BY day, plate_index, well_id, added_at"
        ).fetchall()
        return [
            {
                "path": r["relative_path"],
                "day": r["day"],
                "plate": r["plate_index"],
                "well": r["well_id"],
            }
            for r in rows
        ]

    def get_full_photo_path(self, relative_path: str) -> str:
        parts = relative_path.replace("\\", "/").split("/")
        return os.path.join(self.project_dir, *parts)

    def get_report_snapshot(self) -> Dict[str, Any]:
        data = self.get_full_project_data()
        data["plate_format"] = self.get_plate_format()
        data["concentration_map"] = self.get_concentration_map()
        data["plate_dimensions"] = self.get_plate_dimensions()
        data["photos_with_metadata"] = self.get_all_photos_with_metadata()
        data["water_quality_log"] = self.get_water_quality_log()
        data["analysis_settings"] = self.get_analysis_settings()
        return data

    # ------------------------------------------------------------------
    # Registry sync
    # ------------------------------------------------------------------

    def _sync_to_registry(self) -> None:
        """Update (or insert) this project's row in registry.db."""
        try:
            from src.core.utils import get_registry_db_path
            from src.database.registry import ProjectRegistry

            info = self.get_project_info()
            completed_count = len(info.get("completed_days", []))
            registry = ProjectRegistry(get_registry_db_path())
            try:
                registry.upsert_project(
                    {
                        "project_name": info.get("project_name", self._project_name),
                        "db_path": self.db_path,
                        "main_researcher": info.get("main_researcher", ""),
                        "substance": info.get("substance", ""),
                        "num_days": info.get("num_days", 4),
                        "completed_days_count": completed_count,
                        "start_date": info.get("start_date", ""),
                        "last_modified": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    }
                )
            finally:
                registry.close()
        except Exception as e:
            # Non-fatal: a registry failure must not stop the project itself
            # working. Logged with a traceback because the hub silently going
            # stale is otherwise indistinguishable from nothing having changed.
            log.error(f"Could not sync to registry: {e}", exc_info=True)

    def remove_from_registry(self) -> None:
        """Remove this project from registry.db (call before deleting files)."""
        try:
            from src.core.utils import get_registry_db_path
            from src.database.registry import ProjectRegistry
            registry = ProjectRegistry(get_registry_db_path())
            try:
                registry.remove_project(self.get_project_name())
            finally:
                registry.close()
        except Exception as e:
            log.warning(f"Could not remove from registry: {e}")

    # ------------------------------------------------------------------
    # Bulk query helpers (used by analysis, review, and propagation)
    # ------------------------------------------------------------------

    def has_well_data_for_day(self, day: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM well_observations WHERE day = ? LIMIT 1", (day,)
        ).fetchone()
        return row is not None

    def get_well_observations_for_day(self, day: int) -> Dict[str, Dict[str, Any]]:
        """
        Return all well observations for a day as:
            {str(plate_index): {well_id: well_data_dict}}
        Includes statuses, notes, conditions, and photos.
        """
        obs_rows = self._conn.execute(
            "SELECT plate_index, well_id, status, notes, auto_filled FROM well_observations WHERE day = ?",
            (day,),
        ).fetchall()

        result: Dict[str, Dict[str, Any]] = {}
        for r in obs_rows:
            plate_str = str(r["plate_index"])
            well_id = r["well_id"]
            result.setdefault(plate_str, {})[well_id] = {
                "status": r["status"],
                "notes": r["notes"],
                "auto_filled": r["auto_filled"],
                "sublethal_conditions": [],
                "lethal_conditions": [],
                "photos": [],
            }

        # Fetch conditions and photos in batch
        sub_rows = self._conn.execute(
            "SELECT plate_index, well_id, condition FROM well_sublethal_conditions WHERE day = ?", (day,)
        ).fetchall()
        for r in sub_rows:
            plate_str = str(r["plate_index"])
            if plate_str in result and r["well_id"] in result[plate_str]:
                result[plate_str][r["well_id"]]["sublethal_conditions"].append(r["condition"])

        let_rows = self._conn.execute(
            "SELECT plate_index, well_id, condition FROM well_lethal_conditions WHERE day = ?", (day,)
        ).fetchall()
        for r in let_rows:
            plate_str = str(r["plate_index"])
            if plate_str in result and r["well_id"] in result[plate_str]:
                result[plate_str][r["well_id"]]["lethal_conditions"].append(r["condition"])

        photo_rows = self._conn.execute(
            "SELECT plate_index, well_id, relative_path FROM well_photos WHERE day = ? ORDER BY added_at",
            (day,),
        ).fetchall()
        for r in photo_rows:
            plate_str = str(r["plate_index"])
            if plate_str in result and r["well_id"] in result[plate_str]:
                result[plate_str][r["well_id"]]["photos"].append(r["relative_path"])

        return result

    def propagate_day_data(self, from_day: int, to_day: int) -> None:
        """
        Copy well observations from from_day into to_day,
        carrying statuses but clearing notes and sublethal conditions.
        Only runs if to_day has no existing observations.
        """
        if self.has_well_data_for_day(to_day):
            return
        prev_obs = self.get_well_observations_for_day(from_day)
        with self._conn:
            for plate_str, wells in prev_obs.items():
                plate_idx = int(plate_str)
                for well_id, wd in wells.items():
                    self._conn.execute(
                        """
                        INSERT OR IGNORE INTO well_observations
                            (day, plate_index, well_id, status, notes, auto_filled)
                        VALUES (?, ?, ?, ?, '', 1)
                        """,
                        (to_day, plate_idx, well_id, wd["status"]),
                    )
            # Propagate lethal conditions — OECD TG 236: a lethal endpoint confirmed
            # on day N remains valid on all subsequent days for that well.
            prev_lethal = self._conn.execute(
                "SELECT plate_index, well_id, condition FROM well_lethal_conditions WHERE day = ?",
                (from_day,),
            ).fetchall()
            for row in prev_lethal:
                plate_str = str(row["plate_index"])
                well_status = prev_obs.get(plate_str, {}).get(row["well_id"], {}).get("status", "")
                if well_status not in _DEAD_STATUSES:
                    continue
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO well_lethal_conditions
                        (day, plate_index, well_id, condition)
                    VALUES (?, ?, ?, ?)
                    """,
                    (to_day, row["plate_index"], row["well_id"], row["condition"]),
                )

    def _ensure_observation_row(self, day: int, plate_index: int, well_id: str) -> None:
        """
        Guarantee a well_observations row exists for (day, plate, well).
        If not present, insert a carried-forward placeholder (auto_filled=1):
          - day 1: status = Live Embryo
          - day N: copy previous day's status + lethal conditions when dead
        Never overwrites an existing row.
        """
        exists = self._conn.execute(
            "SELECT 1 FROM well_observations WHERE day=? AND plate_index=? AND well_id=?",
            (day, plate_index, well_id),
        ).fetchone()
        if exists:
            return

        from src.core.constants import STATUS_LIVE_EMBRYO, DEAD_STATUSES
        if day <= 1:
            carry_status = STATUS_LIVE_EMBRYO
            carry_lethal: List[str] = []
        else:
            prev = self.get_well_data(day - 1, plate_index, well_id)
            carry_status = prev.get("status", STATUS_LIVE_EMBRYO)
            carry_lethal = prev.get("lethal_conditions", []) if carry_status in DEAD_STATUSES else []

        with self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO well_observations
                    (day, plate_index, well_id, status, notes, auto_filled)
                VALUES (?, ?, ?, ?, '', 1)
                """,
                (day, plate_index, well_id, carry_status),
            )
            for cond in carry_lethal:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO well_lethal_conditions
                        (day, plate_index, well_id, condition)
                    VALUES (?, ?, ?, ?)
                    """,
                    (day, plate_index, well_id, cond),
                )

    def _backfill_legacy_finalized_days(self) -> None:
        """Materialize completed days that were finalized before the day-end model.

        Versions prior to 2.1.4 wrote an observation row only where the operator
        *changed* a well, so a well left at its carried-forward live status has no
        row at all. Finalizing declares a day fully observed, so those wells were
        examined; the analysis accounts for this, but the stored data does not,
        leaving the report's summary table and its raw-data appendix quoting
        different counts for the same day, and the CSV export short of rows.

        `materialize_day` writes exactly what a current-version finalize would
        have written: it fills only wells lacking a row, marks them
        ``auto_filled=1`` so they stay distinguishable and ``reopen_day`` can
        still remove them, and never touches an operator-entered row. Days are
        processed in ascending order because each carries its status forward from
        the one before.

        Runs on open and is idempotent: once a project is complete this costs two
        COUNT queries and does nothing.
        """
        try:
            assigned = self._conn.execute(
                "SELECT COUNT(*) FROM plate_layout"
            ).fetchone()[0]
            if not assigned:
                return

            completed = [
                r["day"] for r in self._conn.execute(
                    "SELECT day FROM completed_days ORDER BY day"
                ).fetchall()
            ]
            if not completed:
                return

            recorded = {
                r["day"]: r["n"] for r in self._conn.execute(
                    "SELECT day, COUNT(*) AS n FROM well_observations GROUP BY day"
                ).fetchall()
            }
            incomplete = [d for d in completed if recorded.get(d, 0) < assigned]
            if not incomplete:
                return

            for day in incomplete:  # ascending: each carries forward from the last
                self.materialize_day(day)
            log.info(
                "Backfilled %d finalized day(s) recorded before day-end "
                "materialization: %s", len(incomplete), incomplete,
            )
        except Exception as e:
            # A project must still open even if the backfill cannot run; the
            # analysis accounts for the missing rows regardless.
            log.warning(f"Could not backfill legacy finalized days: {e}")

    def materialize_day(self, day: int) -> None:
        """
        For every assigned well lacking a row on `day`, insert a carried-forward
        placeholder (auto_filled=1). Never overwrites existing user-entered rows.
        Also propagates lethal conditions forward for dead wells, matching the
        OECD TG 236 requirement that confirmed lethal endpoints remain valid.
        """
        from src.core.constants import STATUS_LIVE_EMBRYO, DEAD_STATUSES
        layouts = self.get_all_plate_layouts()
        prev_obs = self.get_well_observations_for_day(day - 1) if day > 1 else {}

        def _do():
            for plate_str, wells in layouts.items():
                plate_idx = int(plate_str)
                for well_id in wells:
                    exists = self._conn.execute(
                        "SELECT 1 FROM well_observations WHERE day=? AND plate_index=? AND well_id=?",
                        (day, plate_idx, well_id),
                    ).fetchone()
                    if exists:
                        continue

                    if day <= 1:
                        carry_status = STATUS_LIVE_EMBRYO
                        carry_lethal: List[str] = []
                    else:
                        prev_well = prev_obs.get(plate_str, {}).get(well_id, {})
                        carry_status = prev_well.get("status", STATUS_LIVE_EMBRYO)
                        carry_lethal = (
                            prev_well.get("lethal_conditions", [])
                            if carry_status in DEAD_STATUSES
                            else []
                        )

                    self._conn.execute(
                        """
                        INSERT OR IGNORE INTO well_observations
                            (day, plate_index, well_id, status, notes, auto_filled)
                        VALUES (?, ?, ?, ?, '', 1)
                        """,
                        (day, plate_idx, well_id, carry_status),
                    )
                    for cond in carry_lethal:
                        self._conn.execute(
                            """
                            INSERT OR IGNORE INTO well_lethal_conditions
                                (day, plate_index, well_id, condition)
                            VALUES (?, ?, ?, ?)
                            """,
                            (day, plate_idx, well_id, cond),
                        )

        self._transact(_do)
        log.info(f"Day {day} materialized (untouched wells filled with auto_filled=1).")

    def finalize_day(self, day: int) -> None:
        """Materialize all untouched wells then mark the day as completed."""
        self.materialize_day(day)
        self.set_day_as_completed(day)

    def reopen_day(self, day: int) -> None:
        """
        Reopen `day` for editing (cascade): mark day and all later finalized days
        incomplete. For days strictly after `day`, delete auto_filled=1 rows only
        (user-entered rows and their associated conditions are preserved).
        """
        info = self.get_project_info()
        num_days = info.get("num_days", 1)

        def _do():
            self._conn.execute("DELETE FROM completed_days WHERE day >= ?", (day,))
            for later in range(day + 1, num_days + 1):
                # Remove auto-filled observations and their orphaned conditions
                auto_wells = self._conn.execute(
                    """
                    SELECT plate_index, well_id FROM well_observations
                    WHERE day=? AND auto_filled=1
                    """,
                    (later,),
                ).fetchall()
                for row in auto_wells:
                    self._conn.execute(
                        "DELETE FROM well_lethal_conditions WHERE day=? AND plate_index=? AND well_id=?",
                        (later, row["plate_index"], row["well_id"]),
                    )
                    self._conn.execute(
                        "DELETE FROM well_sublethal_conditions WHERE day=? AND plate_index=? AND well_id=?",
                        (later, row["plate_index"], row["well_id"]),
                    )
                self._conn.execute(
                    "DELETE FROM well_observations WHERE day=? AND auto_filled=1", (later,)
                )

        self._transact(_do)
        self._sync_to_registry()
        log.info(f"Day {day} reopened; auto-filled rows removed for days > {day}.")

    def get_all_well_observations_with_layout(self) -> List[Dict[str, Any]]:
        """
        Returns all well observations joined with plate layout and concentration info.
        Used by the results analysis worker to build a DataFrame.
        """
        rows = self._conn.execute(
            """
            SELECT
                wo.day, wo.plate_index, wo.well_id, wo.status, wo.notes,
                pl.conc_id,
                cg.type AS conc_type,
                cg.value AS conc_value,
                COUNT(DISTINCT wsc.rowid) AS sublethal_count,
                GROUP_CONCAT(DISTINCT wsc.condition) AS sublethal_conditions,
                GROUP_CONCAT(DISTINCT wlc.condition) AS lethal_conditions
            FROM well_observations wo
            LEFT JOIN plate_layout pl ON pl.plate_index = wo.plate_index AND pl.well_id = wo.well_id
            LEFT JOIN concentration_groups cg ON cg.id = pl.conc_id
            LEFT JOIN well_sublethal_conditions wsc
                ON wsc.day = wo.day AND wsc.plate_index = wo.plate_index AND wsc.well_id = wo.well_id
            LEFT JOIN well_lethal_conditions wlc
                ON wlc.day = wo.day AND wlc.plate_index = wo.plate_index AND wlc.well_id = wo.well_id
            GROUP BY wo.day, wo.plate_index, wo.well_id, wo.status, wo.notes,
                     pl.conc_id, cg.type, cg.value
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def get_full_project_data(self) -> Dict[str, Any]:
        """
        Assemble and return a complete project data dict compatible with the old
        JSON schema. Used by report_generator.py.
        """
        info = self.get_project_info()
        data: Dict[str, Any] = {
            "project_name": info.get("project_name", ""),
            "main_researcher": info.get("main_researcher", ""),
            "substance": info.get("substance", ""),
            "concentration_unit": info.get("concentration_unit", "mg/L"),
            "start_date": info.get("start_date", ""),
            "num_days": info.get("num_days", 4),
            "num_plates": info.get("num_plates", 1),
            "report_notes": info.get("report_notes", ""),
            "completed_days": self.get_completed_days(),
            "substance_details": self.get_substance_details(),
            "test_conditions": self.get_test_conditions(),
            "test_organisms": self.get_test_organisms(),
            "methodology": self.get_methodology(),
            "concentration_settings": {
                "concentrations": self.get_concentrations(),
                **self.get_concentration_settings(),
            },
            "plate_layout": self.get_all_plate_layouts(),
            "well_data": {},
        }
        # Build well_data for all days
        num_days = info.get("num_days", 4)
        for day in range(1, num_days + 1):
            day_data = self.get_well_observations_for_day(day)
            if day_data:
                data["well_data"][str(day)] = day_data
        return data

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_initial_data(self, data: dict) -> None:
        """Populate all tables from the initial_data dict produced by ProjectCreationPage."""
        # project and substance_details have no INSERT fallback in their setters.
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO project
                    (id, project_name, main_researcher, substance,
                     concentration_unit, start_date, num_days, num_plates,
                     plate_format, report_notes)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    project_name       = excluded.project_name,
                    main_researcher    = excluded.main_researcher,
                    substance          = excluded.substance,
                    concentration_unit = excluded.concentration_unit,
                    start_date         = excluded.start_date,
                    num_days           = excluded.num_days,
                    num_plates         = excluded.num_plates,
                    plate_format       = excluded.plate_format,
                    report_notes       = excluded.report_notes
                """,
                (
                    data.get("project_name", self._project_name),
                    data.get("main_researcher", ""),
                    data.get("substance", ""),
                    data.get("concentration_unit", "mg/L"),
                    data.get("start_date", ""),
                    data.get("num_days", 4),
                    data.get("num_plates", 1),
                    data.get("plate_format", "96-well"),
                    data.get("report_notes", ""),
                ),
            )
            sd = data.get("substance_details", {})
            self._conn.execute(
                """
                INSERT INTO substance_details
                    (id, cas_number, molecular_weight, purity, supplier,
                     physical_appearance, water_solubility, iupac_name,
                     solvent_used, positive_control_substance)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    cas_number                 = excluded.cas_number,
                    molecular_weight           = excluded.molecular_weight,
                    purity                     = excluded.purity,
                    supplier                   = excluded.supplier,
                    physical_appearance        = excluded.physical_appearance,
                    water_solubility           = excluded.water_solubility,
                    iupac_name                 = excluded.iupac_name,
                    solvent_used               = excluded.solvent_used,
                    positive_control_substance = excluded.positive_control_substance
                """,
                (
                    sd.get("cas_number", ""), sd.get("molecular_weight", ""),
                    sd.get("purity", ""), sd.get("supplier", ""),
                    sd.get("physical_appearance", ""), sd.get("water_solubility", ""),
                    sd.get("iupac_name", ""), sd.get("solvent_used", ""),
                    sd.get("positive_control_substance", ""),
                ),
            )
            for day in data.get("completed_days", []):
                self._conn.execute(
                    "INSERT OR IGNORE INTO completed_days (day) VALUES (?)", (day,)
                )

        # Delegate to existing transactional setters for all other tables.
        self.update_test_conditions(**data.get("test_conditions", {}))
        self.update_test_organisms(**data.get("test_organisms", {}))
        self.update_methodology(**data.get("methodology", {}))

        conc_settings = data.get("concentration_settings", {})
        concentrations = conc_settings.get("concentrations", [])
        if concentrations:
            self.set_concentrations(
                concentrations,
                conc_settings.get("required_embryos", 20),
                conc_settings.get("required_plates", 1),
            )

        plate_layout = data.get("plate_layout", {})
        if plate_layout:
            self.commit_plate_layout(plate_layout)

        # well_data is only populated on import (not during normal project creation).
        for day_str, plates in data.get("well_data", {}).items():
            for plate_str, wells in plates.items():
                for well_id, wd in wells.items():
                    day, plate_idx = int(day_str), int(plate_str)
                    self.save_well_data(
                        day, plate_idx, well_id,
                        wd.get("status", "Live Embryo"),
                        wd.get("sublethal_conditions", []),
                        wd.get("lethal_conditions", []),
                        wd.get("notes", ""),
                    )
                    for rel_path in wd.get("photos", []):
                        with self._conn:
                            self._conn.execute(
                                "INSERT OR IGNORE INTO well_photos"
                                " (day, plate_index, well_id, relative_path, added_at)"
                                " VALUES (?,?,?,?,?)",
                                (day, plate_idx, well_id, rel_path,
                                 datetime.datetime.now(datetime.timezone.utc).isoformat()),
                            )
