import datetime
import logging
import os
import random
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

from src.database.schema import initialize_project_db
from src.database.migrations import MigrationRunner
from src.core.constants import LIVE_STATUSES as _LIVE_STATUSES, DEAD_STATUSES as _DEAD_STATUSES

log = logging.getLogger(__name__)

# Subdirectory names inside each project folder
PHOTOS_SUBDIR = "photos"
REPORTS_SUBDIR = "reports"


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

        self._conn = self._open_connection()
        self._conc_map_cache: Optional[Dict[str, Any]] = None
        initialize_project_db(self._conn)
        MigrationRunner(self._conn).run()
        self._ensure_subdirs()

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

    def _ensure_subdirs(self) -> None:
        os.makedirs(os.path.join(self.project_dir, PHOTOS_SUBDIR), exist_ok=True)
        os.makedirs(os.path.join(self.project_dir, REPORTS_SUBDIR), exist_ok=True)

    def close(self) -> None:
        """Close the database connection. Call when done with the project."""
        try:
            self._conn.close()
        except sqlite3.ProgrammingError:
            pass  # Already closed
        except Exception as e:
            log.warning(f"Error closing database connection: {e}")

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
        }
        to_set = {k: v for k, v in fields.items() if k in allowed}
        if not to_set:
            return
        cols = ", ".join(f"{k} = ?" for k in to_set)
        with self._conn:
            self._conn.execute("INSERT OR IGNORE INTO test_conditions (id) VALUES (1)")
            self._conn.execute(
                f"UPDATE test_conditions SET {cols} WHERE id = 1",
                list(to_set.values()),
            )

    def get_test_organisms(self) -> Dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM test_organisms WHERE id = 1"
        ).fetchone()
        return dict(row) if row else {}

    def update_test_organisms(self, **fields) -> None:
        allowed = {"strain", "source", "collection_method"}
        to_set = {k: v for k, v in fields.items() if k in allowed}
        if not to_set:
            return
        cols = ", ".join(f"{k} = ?" for k in to_set)
        with self._conn:
            self._conn.execute("INSERT OR IGNORE INTO test_organisms (id) VALUES (1)")
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
        if not to_set:
            return
        cols = ", ".join(f"{k} = ?" for k in to_set)
        with self._conn:
            self._conn.execute("INSERT OR IGNORE INTO methodology (id) VALUES (1)")
            self._conn.execute(
                f"UPDATE methodology SET {cols} WHERE id = 1",
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
        if self._conc_map_cache is None:
            self._conc_map_cache = {c["id"]: c for c in self.get_concentrations()}
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

        for r in rows:
            cid = r["conc_id"]
            if cid not in results:
                continue
            results[cid]["total"] += 1
            status = r["status"]
            if status in _LIVE_STATUSES:
                results[cid]["live"] += 1
            elif status in _DEAD_STATUSES:
                results[cid]["dead"] += 1
            # "Absent (use majority)" counts toward total but not live/dead
            if r["sublethal_count"] > 0:
                results[cid]["malformed"] += 1

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

            unique_filename = f"{well_id}_Plate{plate_index + 1}_{uuid.uuid4().hex[:8]}.jpg"
            destination_path = os.path.join(day_photo_dir, unique_filename)

            with Image.open(source_photo_path) as img:
                rgb_img = img.convert("RGB")
                rgb_img.save(destination_path, "jpeg", quality=95)

            relative_path = os.path.join(PHOTOS_SUBDIR, f"Day_{day}", unique_filename)
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
            rel = os.path.relpath(
                os.path.normpath(full_photo_path),
                os.path.normpath(self.project_dir),
            ).replace("\\", "/")
            with self._conn:
                cur = self._conn.execute(
                    "DELETE FROM well_photos WHERE relative_path = ?", (rel,)
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
            log.warning(f"Could not sync to registry: {e}")

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
