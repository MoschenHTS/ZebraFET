"""
test_project_io.py — Verify export/import round-trip for ZebraFET archives.

Tests that:
  - export_project() creates a valid ZIP with the .zfet extension
  - The archive contains a .db file at the root
  - All concentration groups, plate layouts, and well observations
    survive a round-trip (export → manual extract → reopen, and separately
    export → import_project())
  - Both .zfet and legacy .zebravet extensions produce valid archives
    (zipfile is extension-agnostic)
"""
import os
import sqlite3
import zipfile
import pytest

import src.core.utils as utils
from src.core.project_manager import ProjectManager
from src.core.project_exporter import export_project, import_project, is_importable_archive
from src.core.constants import STATUS_DEAD_EMBRYO, STATUS_LIVE_EMBRYO, STATUS_LIVE_HATCHED
from src.database.registry import ProjectRegistry
from src.database.schema import CURRENT_SCHEMA_VERSION


def test_isolated_app_data_fixture_points_at_tmp_path(tmp_path):
    """The autouse fixture in conftest.py must redirect both resolvers.

    Asserted directly against tmp_path rather than by disabling the fixture and
    comparing against the real path: that comparison is not a safe check to
    make even once, since disabling it for the comparison writes a real project
    into the developer's actual ZebraFET application data as a side effect of
    running the test.
    """
    assert utils.get_registry_db_path().startswith(str(tmp_path))
    assert utils.get_projects_base_dir().startswith(str(tmp_path))


INITIAL_DATA = {
    "project_name": "TestIO",
    "main_researcher": "Test Researcher",
    "substance": "TestSubstance",
    "num_days": 4,
    "num_plates": 1,
}

CONCENTRATIONS = [
    {"id": "ctrl", "type": "Control",   "value": 0.0, "replicates": 1, "wells": 4, "per_plate": True},
    {"id": "s1",   "type": "Substrate", "value": 1.0, "replicates": 1, "wells": 4, "per_plate": True},
    {"id": "s2",   "type": "Substrate", "value": 2.0, "replicates": 1, "wells": 4, "per_plate": True},
]

LAYOUT = {
    "1": {
        "A1": "ctrl", "A2": "ctrl",
        "B1": "s1",   "B2": "s1",
        "C1": "s2",   "C2": "s2",
    }
}


def _build_project(tmp_path) -> ProjectManager:
    """Create a fully populated project for IO testing."""
    proj_dir = str(tmp_path / "TestIO")
    m = ProjectManager.create_new(proj_dir, INITIAL_DATA)
    m.set_concentrations(CONCENTRATIONS, required_embryos=12, required_plates=1)
    m.commit_plate_layout(LAYOUT)
    m.save_well_data(
        day=1, plate_index=1, well_id="A1",
        status=STATUS_LIVE_EMBRYO,
        sublethal_conditions=[], lethal_conditions=[], notes="control note",
    )
    m.save_well_data(
        day=1, plate_index=1, well_id="B1",
        status=STATUS_DEAD_EMBRYO,
        sublethal_conditions=["Yolk sac oedema"],
        lethal_conditions=["Lack of heartbeat"],
        notes="",
    )
    m.save_well_data(
        day=1, plate_index=1, well_id="C1",
        status=STATUS_LIVE_HATCHED,
        sublethal_conditions=[], lethal_conditions=[], notes="",
    )
    return m


class TestExportArchive:
    def test_export_creates_file(self, tmp_path):
        m = _build_project(tmp_path)
        try:
            archive = str(tmp_path / "export.zfet")
            export_project(m, archive)
            assert os.path.isfile(archive)
        finally:
            m.close()

    def test_archive_is_valid_zip(self, tmp_path):
        m = _build_project(tmp_path)
        try:
            archive = str(tmp_path / "export.zfet")
            export_project(m, archive)
            assert zipfile.is_zipfile(archive)
        finally:
            m.close()

    def test_archive_contains_db_at_root(self, tmp_path):
        m = _build_project(tmp_path)
        try:
            archive = str(tmp_path / "export.zfet")
            export_project(m, archive)
            with zipfile.ZipFile(archive) as zf:
                names = zf.namelist()
            db_entries = [n for n in names if n.endswith(".db") and "/" not in n]
            assert len(db_entries) == 1
            assert db_entries[0] == "TestIO.db"
        finally:
            m.close()

    def test_zebravet_extension_also_valid_zip(self, tmp_path):
        """Extension should not affect zipfile validity."""
        m = _build_project(tmp_path)
        try:
            archive = str(tmp_path / "export.zebravet")
            export_project(m, archive)
            assert zipfile.is_zipfile(archive)
        finally:
            m.close()


class TestImportExtensions:
    """The import entry points accept re-wrapped archives.

    Mail providers and cloud storage detect the ZIP payload of a .zfet and
    append ".zip" on download, so a shared "MyProject.zfet" reaches a coworker
    as "MyProject.zfet.zip". Every UI entry point routes through
    is_importable_archive(), so it must accept those names.
    """

    def test_accepts_native_and_legacy(self):
        assert is_importable_archive("MyProject.zfet")
        assert is_importable_archive("MyProject.zebravet")

    def test_accepts_rewrapped_zip_names(self):
        assert is_importable_archive("MyProject.zfet.zip")
        assert is_importable_archive("MyProject.zebravet.zip")
        assert is_importable_archive("MyProject.zip")

    def test_case_insensitive(self):
        assert is_importable_archive("MyProject.ZFET")
        assert is_importable_archive("MyProject.Zfet.Zip")

    def test_rejects_unrelated_files(self):
        assert not is_importable_archive("report.docx")
        assert not is_importable_archive("data.csv")
        assert not is_importable_archive("MyProject.db")
        assert not is_importable_archive("/some/directory")

    def test_rewrapped_archive_is_still_a_valid_zip(self, tmp_path):
        """A .zfet exported and renamed .zfet.zip stays a valid, openable ZIP
        whose root .db is discoverable (mirrors import_project's check)."""
        m = _build_project(tmp_path)
        try:
            archive = str(tmp_path / "TestIO.zfet.zip")
            export_project(m, archive)
        finally:
            m.close()
        assert is_importable_archive(archive)
        assert zipfile.is_zipfile(archive)
        with zipfile.ZipFile(archive) as zf:
            db_entries = [n for n in zf.namelist() if n.endswith(".db") and "/" not in n]
        assert db_entries == ["TestIO.db"]


class TestRoundTrip:
    def _export_and_reopen(self, tmp_path, extension=".zfet"):
        """Export a project, extract it to a new directory, and reopen."""
        m = _build_project(tmp_path)
        archive = str(tmp_path / f"export{extension}")
        try:
            export_project(m, archive)
        finally:
            m.close()

        # Extract the archive manually (mirrors what import_project does).
        # ProjectManager derives db_path from os.path.basename(project_dir), so
        # the directory must be named after the project ("TestIO") so that
        # ProjectManager("…/TestIO") → looks for "…/TestIO/TestIO.db".
        restore_dir = str(tmp_path / "restored" / "TestIO")
        os.makedirs(restore_dir, exist_ok=True)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(restore_dir)

        # Reopen as a ProjectManager
        restored = ProjectManager(restore_dir)
        return restored

    def test_project_name_survives_round_trip(self, tmp_path):
        m = self._export_and_reopen(tmp_path)
        try:
            assert m.get_project_name() == "TestIO"
        finally:
            m.close()

    def test_concentration_groups_survive_round_trip(self, tmp_path):
        m = self._export_and_reopen(tmp_path)
        try:
            concs = m.get_concentrations()
            ids = {c["id"] for c in concs}
            assert "ctrl" in ids
            assert "s1" in ids
            assert "s2" in ids
        finally:
            m.close()

    def test_plate_layout_survives_round_trip(self, tmp_path):
        m = self._export_and_reopen(tmp_path)
        try:
            layout = m.get_plate_layout(1)
            assert layout.get("A1") == "ctrl"
            assert layout.get("B1") == "s1"
            assert layout.get("C1") == "s2"
        finally:
            m.close()

    def test_well_observations_survive_round_trip(self, tmp_path):
        m = self._export_and_reopen(tmp_path)
        try:
            wd = m.get_well_data(1, 1, "B1")
            assert wd["status"] == STATUS_DEAD_EMBRYO
            assert "Yolk sac oedema" in wd["sublethal_conditions"]
            assert "Lack of heartbeat" in wd["lethal_conditions"]
        finally:
            m.close()

    def test_live_hatched_survives_round_trip(self, tmp_path):
        m = self._export_and_reopen(tmp_path)
        try:
            wd = m.get_well_data(1, 1, "C1")
            assert wd["status"] == STATUS_LIVE_HATCHED
        finally:
            m.close()

    def test_aggregation_survives_round_trip(self, tmp_path):
        """After round-trip, get_results_data() should still return correct counts."""
        m = self._export_and_reopen(tmp_path)
        try:
            results = m.get_results_data(1)
            assert results["ctrl"]["live"] == 1
            assert results["s1"]["dead"] == 1
            assert results["s2"]["live"] == 1  # live hatched = live
        finally:
            m.close()

    def test_zebravet_archive_round_trip(self, tmp_path):
        """Legacy .zebravet extension should import identically."""
        m = self._export_and_reopen(tmp_path, extension=".zebravet")
        try:
            concs = m.get_concentrations()
            assert len(concs) == 3
        finally:
            m.close()


class TestImportProject:
    """Exercises import_project() itself, rather than the manual extraction
    TestRoundTrip uses to test the archive layout independently of it.

    Safe to call directly now that the _isolated_app_data fixture (conftest.py)
    redirects get_projects_base_dir() and get_registry_db_path() into tmp_path,
    so this covers behaviour no test previously touched: the directory
    import_project() chooses, the schema migration it runs on open, and the
    registry entry it creates.
    """

    def _export_archive(self, tmp_path) -> str:
        m = _build_project(tmp_path)
        archive = str(tmp_path / "export.zfet")
        try:
            export_project(m, archive)
        finally:
            m.close()
        return archive

    def test_returns_a_directory_named_after_the_project(self, tmp_path):
        archive = self._export_archive(tmp_path)
        project_dir = import_project(archive)
        assert os.path.basename(project_dir) == "TestIO"

    def test_imported_project_reopens_with_its_data_intact(self, tmp_path):
        archive = self._export_archive(tmp_path)
        project_dir = import_project(archive)

        m = ProjectManager(project_dir)
        try:
            assert m.get_project_name() == "TestIO"
            wd = m.get_well_data(1, 1, "B1")
            assert wd["status"] == STATUS_DEAD_EMBRYO
        finally:
            m.close()

    def test_import_registers_the_project(self, tmp_path):
        """import_project() must sync to the registry, not just extract files.

        get_registry_db_path() is looked up via the utils module at call time
        (utils.get_registry_db_path()), not imported by name at module level —
        the latter would bind the pre-patch function before the
        _isolated_app_data fixture runs and silently read the developer's real
        registry instead of the sandboxed one.
        """
        archive = self._export_archive(tmp_path)
        import_project(archive)

        registry = ProjectRegistry(utils.get_registry_db_path())
        try:
            names = {row["project_name"] for row in registry._conn.execute(
                "SELECT project_name FROM projects"
            )}
        finally:
            registry.close()
        assert "TestIO" in names

    def test_a_second_import_of_the_same_project_name_is_rejected(self, tmp_path):
        archive = self._export_archive(tmp_path)
        import_project(archive)
        with pytest.raises(FileExistsError):
            import_project(archive)


class TestImportRejectsUnsafeArchiveNames:
    """The destination directory is derived from a name inside the archive.

    Archives are shared by email and cloud storage — the reason a bare .zip is
    accepted at all — so the name cannot be trusted. Screening the entry for "/"
    alone let a database called "..\\..\\evil.db" through: ZIP entries may carry
    backslashes, and on Windows that name sends the destination outside the
    projects directory, after which the per-entry check validates every file
    against an already-escaped root.
    """

    def _archive_with_db_named(self, tmp_path, db_name: str) -> str:
        archive = str(tmp_path / "crafted.zfet")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(db_name, b"SQLite format 3\x00")
        return archive

    @pytest.mark.parametrize("db_name", [
        "..\\..\\evil.db",
        "..\\evil.db",
        "sub\\dir\\evil.db",
    ])
    def test_traversing_names_are_refused(self, tmp_path, db_name):
        with pytest.raises(ValueError, match="unsafe project database name"):
            import_project(self._archive_with_db_named(tmp_path, db_name))

    def test_a_genuine_archive_is_still_accepted(self, tmp_path):
        """The guard must not reject the names real archives carry."""
        m = _build_project(tmp_path)
        archive = str(tmp_path / "export.zfet")
        try:
            export_project(m, archive)
        finally:
            m.close()
        assert os.path.basename(import_project(archive)) == "TestIO"


class TestAnalysisSettingsPersistence:
    """Model, Abbott and reference control materially change the reported LC50
    and NOEC, so re-deriving them from UI defaults on every launch made a saved
    project's analysis irreproducible. Schema v9 stores them with the project.
    """

    def test_defaults_when_never_saved(self, tmp_path):
        m = ProjectManager.create_new(str(tmp_path / "Defaults"), {"project_name": "Defaults"})
        try:
            settings = m.get_analysis_settings()
        finally:
            m.close()
        assert settings["model_mode"] == "LL4"
        assert settings["control_mode"] == "pooled"
        assert settings["abbott"] is False

    def test_round_trip_across_reopen(self, tmp_path):
        path = str(tmp_path / "Persist")
        m = ProjectManager.create_new(path, {"project_name": "Persist"})
        m.save_analysis_settings(model_mode="auto", abbott=True,
                                 control_mode="solvent", bottom=5.0, top=95.0)
        m.close()

        m2 = ProjectManager(path)
        try:
            settings = m2.get_analysis_settings()
        finally:
            m2.close()
        assert settings["model_mode"] == "auto"
        assert settings["abbott"] is True
        assert settings["control_mode"] == "solvent"
        assert settings["bottom"] == 5.0
        assert settings["top"] == 95.0

    def test_unknown_keys_are_ignored(self, tmp_path):
        m = ProjectManager.create_new(str(tmp_path / "Unknown"), {"project_name": "Unknown"})
        try:
            m.save_analysis_settings(model_mode="auto", not_a_column="boom")
            assert m.get_analysis_settings()["model_mode"] == "auto"
        finally:
            m.close()

    def test_settings_reach_the_report_snapshot(self, tmp_path):
        m = ProjectManager.create_new(str(tmp_path / "Snap"), {"project_name": "Snap"})
        try:
            m.save_analysis_settings(control_mode="control")
            snapshot = m.get_report_snapshot()
        finally:
            m.close()
        assert snapshot["analysis_settings"]["control_mode"] == "control"

    def test_migration_adds_the_table_to_a_pre_v9_project(self, tmp_path):
        """A project created before v2.2.0 must gain the table on first open."""
        path = str(tmp_path / "Legacy")
        m = ProjectManager.create_new(path, {"project_name": "Legacy"})
        db_path = m.db_path
        m.close()

        # Rewind to the v8 world: drop the table and the recorded version.
        with sqlite3.connect(db_path) as conn:
            conn.execute("DROP TABLE analysis_settings")
            conn.execute("UPDATE schema_version SET version = 8")

        m2 = ProjectManager(path)
        try:
            settings = m2.get_analysis_settings()
            version = m2._conn.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            ).fetchone()[0]
        finally:
            m2.close()
        assert settings["control_mode"] == "pooled"
        # Tracks the constant rather than a literal: the runner replays every
        # pending migration, so a later schema bump leaves the project current,
        # not at the version this migration introduced.
        assert version == CURRENT_SCHEMA_VERSION


class TestLegacyFinalizedDayBackfill:
    """Days finalized before day-end materialization are completed on open.

    Versions prior to 2.1.4 wrote an observation row only where the operator
    changed a well, so a well left at its carried-forward live status has no row.
    Finalizing declares the day fully observed, so the stored data is incomplete
    rather than the day partially scored. Left unrepaired, the report's summary
    table and its raw-data appendix quote different counts for the same day.
    """

    def _legacy_project(self, tmp_path):
        """A project with a finalized day whose untouched wells were never written."""
        m = _build_project(tmp_path)
        m.finalize_day(1)
        assigned = m._conn.execute("SELECT COUNT(*) FROM plate_layout").fetchone()[0]
        # Rewind to the pre-materialization state: drop the derived rows only,
        # exactly the shape an older version would have left behind.
        with m._conn:
            m._conn.execute("DELETE FROM well_observations WHERE day=1 AND auto_filled=1")
        remaining = m._conn.execute(
            "SELECT COUNT(*) FROM well_observations WHERE day=1"
        ).fetchone()[0]
        proj_dir = m.project_dir
        m.close()
        assert remaining < assigned, "fixture did not reproduce a legacy day"
        return proj_dir, assigned, remaining

    def test_open_backfills_a_finalized_day(self, tmp_path):
        proj_dir, assigned, before = self._legacy_project(tmp_path)
        m = ProjectManager(proj_dir)
        try:
            after = m._conn.execute(
                "SELECT COUNT(*) FROM well_observations WHERE day=1"
            ).fetchone()[0]
        finally:
            m.close()
        assert before < assigned
        assert after == assigned

    def test_backfilled_rows_are_marked_derived(self, tmp_path):
        """auto_filled keeps them distinguishable, and reopen_day removable."""
        proj_dir, _, _ = self._legacy_project(tmp_path)
        m = ProjectManager(proj_dir)
        try:
            derived = m._conn.execute(
                "SELECT COUNT(*) FROM well_observations WHERE day=1 AND auto_filled=1"
            ).fetchone()[0]
        finally:
            m.close()
        assert derived > 0

    def test_operator_entered_rows_are_preserved(self, tmp_path):
        """The backfill must never overwrite a well someone actually scored."""
        proj_dir, _, _ = self._legacy_project(tmp_path)
        m = ProjectManager(proj_dir)
        try:
            observations = m.get_well_observations_for_day(1)
            b1 = observations["1"]["B1"]
        finally:
            m.close()
        assert b1["status"] == STATUS_DEAD_EMBRYO
        assert "Lack of heartbeat" in b1.get("lethal_conditions", [])

    def test_is_idempotent(self, tmp_path):
        proj_dir, assigned, _ = self._legacy_project(tmp_path)
        for _ in range(3):
            m = ProjectManager(proj_dir)
            count = m._conn.execute(
                "SELECT COUNT(*) FROM well_observations WHERE day=1"
            ).fetchone()[0]
            m.close()
            assert count == assigned

    def test_unfinalized_days_are_left_alone(self, tmp_path):
        """An in-progress day is genuinely partly scored and must not be filled."""
        m = _build_project(tmp_path)
        proj_dir = m.project_dir
        before = m._conn.execute(
            "SELECT COUNT(*) FROM well_observations WHERE day=1"
        ).fetchone()[0]
        m.close()

        m2 = ProjectManager(proj_dir)
        try:
            after = m2._conn.execute(
                "SELECT COUNT(*) FROM well_observations WHERE day=1"
            ).fetchone()[0]
        finally:
            m2.close()
        assert after == before


class TestTG236FieldMigration:
    """Fields added in v12 for the OECD TG 236 report contents.

    The batch fertilization rate is also a validity criterion (§9a). It is TEXT
    rather than a number so that "not recorded" stays distinguishable from a
    recorded 0%, which is what lets the report omit the criterion entirely.
    """

    def _legacy_project(self, tmp_path):
        """A project as v11 left it: neither column present."""
        path = str(tmp_path / "Legacy")
        m = ProjectManager.create_new(path, {"project_name": "Legacy"})
        db_path = m.db_path
        m.close()
        with sqlite3.connect(db_path) as conn:
            conn.execute("ALTER TABLE test_conditions DROP COLUMN fertilization_rate")
            conn.execute("ALTER TABLE test_organisms DROP COLUMN species")
            conn.execute("UPDATE schema_version SET version = 11")
        return path

    def test_a_pre_v12_project_gains_both_columns_on_open(self, tmp_path):
        m = ProjectManager(self._legacy_project(tmp_path))
        try:
            assert m.get_test_conditions()["fertilization_rate"] == ""
            assert m.get_test_organisms()["species"] == "Danio rerio"
            version = m._conn.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            ).fetchone()[0]
        finally:
            m.close()
        assert version == CURRENT_SCHEMA_VERSION

    def test_migrating_preserves_what_was_already_recorded(self, tmp_path):
        path = self._legacy_project(tmp_path)
        m = ProjectManager(path)
        try:
            m.update_test_conditions(ph="7.4")
            m.update_test_organisms(strain="AB")
        finally:
            m.close()
        m2 = ProjectManager(path)
        try:
            assert m2.get_test_conditions()["ph"] == "7.4"
            assert m2.get_test_organisms()["strain"] == "AB"
        finally:
            m2.close()

    def test_both_fields_round_trip(self, tmp_path):
        path = str(tmp_path / "RoundTrip")
        m = ProjectManager.create_new(path, {"project_name": "RoundTrip"})
        try:
            m.update_test_conditions(fertilization_rate="88 %")
            m.update_test_organisms(species="Oryzias latipes")
        finally:
            m.close()
        m2 = ProjectManager(path)
        try:
            assert m2.get_test_conditions()["fertilization_rate"] == "88 %"
            assert m2.get_test_organisms()["species"] == "Oryzias latipes"
        finally:
            m2.close()

    def test_the_creation_payload_carries_the_fertilization_rate(self, tmp_path):
        """The creation page files it under test_conditions, per TG 236 §42."""
        path = str(tmp_path / "FromWizard")
        m = ProjectManager.create_new(path, {
            "project_name": "FromWizard",
            "test_conditions": {"ph": "7.2", "fertilization_rate": "91"},
        })
        try:
            assert m.get_test_conditions()["fertilization_rate"] == "91"
        finally:
            m.close()


class TestSublethalVocabularyMigration:
    """v13 narrows 'Tail / fin malformation' to 'Tail malformation'.

    The old vocabulary carried it alongside 'Fin malformation or absence', so the
    same observation could be filed under either — the scoring inconsistency the
    structured endpoint list exists to prevent. The condition is stored as the
    literal string, so existing projects have to be rewritten on open.
    """

    OLD_LABEL = "Tail / fin malformation"
    NEW_LABEL = "Tail malformation"
    KEPT_LABEL = "Fin malformation or absence"

    def _legacy_project(self, tmp_path):
        """A v12 project holding both of the overlapping labels."""
        path = str(tmp_path / "Vocab")
        m = ProjectManager.create_new(path, {
            "project_name": "Vocab", "num_days": 4, "num_plates": 1,
            "plate_format": "96-well",
        })
        db_path = m.db_path
        m.close()
        with sqlite3.connect(db_path) as conn:
            for day, well, condition in (
                (1, "A1", self.OLD_LABEL),
                (2, "A1", self.OLD_LABEL),
                (1, "A2", self.KEPT_LABEL),
                (1, "A3", "Yolk sac oedema"),
            ):
                conn.execute(
                    "INSERT INTO well_sublethal_conditions (day, plate_index, well_id, condition) "
                    "VALUES (?, 1, ?, ?)", (day, well, condition),
                )
            conn.execute("UPDATE schema_version SET version = 12")
        return path

    def _conditions(self, manager):
        return sorted(
            r[0] for r in manager._conn.execute(
                "SELECT condition FROM well_sublethal_conditions"
            )
        )

    def test_the_old_label_is_rewritten_on_open(self, tmp_path):
        m = ProjectManager(self._legacy_project(tmp_path))
        try:
            conditions = self._conditions(m)
        finally:
            m.close()
        assert self.OLD_LABEL not in conditions
        assert conditions.count(self.NEW_LABEL) == 2

    def test_the_other_endpoints_are_untouched(self, tmp_path):
        """Only the ambiguous label moves; nothing else is rewritten or dropped."""
        m = ProjectManager(self._legacy_project(tmp_path))
        try:
            conditions = self._conditions(m)
        finally:
            m.close()
        assert conditions.count(self.KEPT_LABEL) == 1
        assert conditions.count("Yolk sac oedema") == 1
        assert len(conditions) == 4, "no rows gained or lost"

    def test_the_schema_version_advances(self, tmp_path):
        m = ProjectManager(self._legacy_project(tmp_path))
        try:
            version = m._conn.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            ).fetchone()[0]
        finally:
            m.close()
        assert version == CURRENT_SCHEMA_VERSION

    def test_every_stored_label_is_a_current_endpoint(self, tmp_path):
        """After migrating, nothing in the table is outside the vocabulary."""
        from src.core.constants import NON_LETHAL_ENDPOINTS

        m = ProjectManager(self._legacy_project(tmp_path))
        try:
            conditions = set(self._conditions(m))
        finally:
            m.close()
        assert conditions <= set(NON_LETHAL_ENDPOINTS)
