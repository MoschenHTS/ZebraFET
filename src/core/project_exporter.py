# project_exporter.py
"""
Export and import ZebraFET projects as self-contained .zfet ZIP archives.

Archive layout:
    MyExperiment.zfet (ZIP, DEFLATED)
    ├── MyExperiment.db      ← all project data
    └── photos/
        └── Day_1/
            └── A1_Plate1_abc123.jpg

Reports are excluded (they are re-generated from data). Import extracts the
archive into the standard projects directory and registers it in registry.db.
Legacy .zebravet archives are also accepted on import.
"""
import logging
import os
import zipfile

log = logging.getLogger(__name__)


def export_project(manager, output_path: str) -> None:
    """
    Create a .zfet ZIP archive for *manager*'s project at *output_path*.

    Includes the project's .db file and the entire photos/ sub-directory.
    Reports are excluded because they can always be regenerated.

    Raises OSError / zipfile.BadZipFile on I/O problems.
    """
    project_name = manager.get_project_name()
    project_dir = manager.get_project_directory()

    # Flush all in-memory WAL pages to the main .db file before copying it.
    # Without this, recent writes that are still in the WAL would be absent
    # from the exported archive (the WAL file itself is not included).
    result = manager._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if result and result[0] != 0:
        log.warning(f"WAL checkpoint may be incomplete (result: {list(result)}). Export may miss recent writes.")

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add the SQLite database
        zf.write(manager.db_path, arcname=f"{project_name}.db")

        # Add photos (relative paths are preserved inside the archive)
        photos_dir = os.path.join(project_dir, "photos")
        if os.path.isdir(photos_dir):
            for root, _dirs, files in os.walk(photos_dir):
                for fname in files:
                    full_path = os.path.join(root, fname)
                    arcname = os.path.relpath(full_path, project_dir)
                    zf.write(full_path, arcname=arcname)

    log.info(f"Project '{project_name}' exported to: {output_path}")


def import_project(archive_path: str) -> str:
    """
    Extract a .zfet (or legacy .zebravet) archive into the standard projects
    directory, run migrations, sync to registry.db, and return the project
    directory path.

    Raises:
        ValueError        – archive does not contain a root-level .db file
        FileExistsError   – a project with the same name already exists on disk
        PermissionError   – cannot create the standard projects directory
        zipfile.BadZipFile – the file is not a valid ZIP archive
    """
    # Lazy imports to avoid circular-import issues at module level
    from src.core.project_manager import ProjectManager  # noqa: PLC0415
    from src.core.utils import get_projects_base_dir  # noqa: PLC0415

    with zipfile.ZipFile(archive_path, "r") as zf:
        names = zf.namelist()

        # Locate the .db file at the archive root (no path separator in name)
        db_entries = [n for n in names if n.endswith(".db") and "/" not in n]
        if not db_entries:
            raise ValueError(
                "Invalid archive: no project database (.db) found at the archive root."
            )

        project_name = os.path.splitext(db_entries[0])[0]
        projects_base = get_projects_base_dir()
        project_dir = os.path.join(projects_base, project_name)

        if os.path.exists(project_dir):
            raise FileExistsError(
                f"A project named '{project_name}' already exists at:\n{project_dir}\n\n"
                "Delete or rename the existing project before importing."
            )

        os.makedirs(project_dir, exist_ok=True)
        zf.extractall(project_dir)

    # Open ProjectManager to run any pending migrations and register in registry
    manager = ProjectManager(project_dir)
    try:
        manager._sync_to_registry()
    finally:
        manager.close()

    log.info(f"Project '{project_name}' imported from '{archive_path}' to: {project_dir}")
    return project_dir
