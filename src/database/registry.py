"""
Global project registry — a lightweight SQLite database that indexes all
projects so the hub page can list them with a single SQL query instead of
opening every project file.
"""
import logging
import sqlite3
from typing import Any

from src.database.schema import initialize_registry_db

log = logging.getLogger(__name__)


class ProjectRegistry:
    """
    Wraps the global registry.db.  One row per project.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            initialize_registry_db(self._conn)
        except Exception:
            self._conn.close()
            raise

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_projects(self) -> list[dict[str, Any]]:
        """Return all registered projects ordered by last_modified descending."""
        rows = self._conn.execute(
            "SELECT * FROM projects ORDER BY last_modified DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def upsert_project(self, meta: dict[str, Any]) -> None:
        """Insert or replace a project row (keyed on project_name)."""
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO projects
                    (project_name, db_path, main_researcher, substance,
                     num_days, completed_days_count, start_date, last_modified)
                VALUES
                    (:project_name, :db_path, :main_researcher, :substance,
                     :num_days, :completed_days_count, :start_date, :last_modified)
                ON CONFLICT(project_name) DO UPDATE SET
                    db_path              = excluded.db_path,
                    main_researcher      = excluded.main_researcher,
                    substance            = excluded.substance,
                    num_days             = excluded.num_days,
                    completed_days_count = excluded.completed_days_count,
                    start_date           = excluded.start_date,
                    last_modified        = excluded.last_modified
                """,
                meta,
            )

    def remove_project(self, project_name: str) -> None:
        """Remove a project from the registry (does not delete files)."""
        with self._conn:
            self._conn.execute(
                "DELETE FROM projects WHERE project_name = ?", (project_name,)
            )

    def close(self) -> None:
        self._conn.close()
