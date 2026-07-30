"""
test_thread_safety.py — ProjectManager is used from more than one thread.

The photo assistant computes its suggestions in the thread pool and photo import
now writes from there too, while the GUI thread reads and writes at the same
time. A single shared connection carries a single transaction, so a pool-thread
read landed inside whatever transaction the GUI thread had open, and two
concurrent writes would have had the inner commit close the outer transaction.
"""
import threading

import pytest

from src.core.project_manager import ProjectManager

CONCENTRATIONS = [
    {"id": "ctrl", "type": "Control", "value": 0.0, "replicates": 1, "wells": 4, "per_plate": True},
    {"id": "s1", "type": "Substrate", "value": 1.0, "replicates": 1, "wells": 4, "per_plate": True},
]

WELLS = [f"A{i}" for i in range(1, 5)] + [f"B{i}" for i in range(1, 5)]


@pytest.fixture
def project(tmp_path):
    m = ProjectManager.create_new(
        str(tmp_path / "Threads"), {"project_name": "Threads", "num_days": 3, "num_plates": 1}
    )
    m.set_concentrations(CONCENTRATIONS, required_embryos=8, required_plates=1)
    m.commit_plate_layout(
        {"1": {w: ("ctrl" if w.startswith("A") else "s1") for w in WELLS}}
    )
    yield m
    m.close()


def _run_concurrently(targets):
    """Run each callable in its own thread; re-raise the first failure."""
    errors = []

    def wrap(fn):
        def runner():
            try:
                fn()
            except Exception as exc:  # surfaced after the join below
                errors.append(exc)
        return runner

    threads = [threading.Thread(target=wrap(fn)) for fn in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a worker thread deadlocked"
    if errors:
        raise errors[0]


class TestConnectionsAreThreadLocal:
    def test_each_thread_gets_its_own(self, project):
        seen = {}

        def record(name):
            def run():
                seen[name] = id(project._conn)
            return run

        _run_concurrently([record("a"), record("b")])
        assert len(set(seen.values())) == 2

    def test_same_thread_reuses_one(self, project):
        assert project._conn is project._conn

    def test_close_disposes_every_connection(self, tmp_path):
        m = ProjectManager.create_new(
            str(tmp_path / "Disposed"), {"project_name": "Disposed", "num_days": 1}
        )
        _run_concurrently([lambda: m.get_project_info(), lambda: m.get_concentrations()])
        opened = len(m._connections)
        assert opened >= 2
        m.close()
        assert m._connections == []


class TestConcurrentAccess:
    def test_reads_during_writes_do_not_raise(self, project):
        def writer():
            for day in (1, 2, 3):
                for well in WELLS:
                    project.save_well_data(day, 1, well, "Live Embryo", [], [], "")

        def reader():
            for _ in range(60):
                project.get_well_observations_for_day(1)
                project.get_all_plate_layouts()
                project.get_concentration_map()

        _run_concurrently([writer, reader, reader])

    def test_concurrent_writers_all_land(self, project):
        def writer(day):
            def run():
                for well in WELLS:
                    project.save_well_data(day, 1, well, "Dead Embryo", [], [], "")
            return run

        _run_concurrently([writer(1), writer(2), writer(3)])
        for day in (1, 2, 3):
            rows = project._conn.execute(
                "SELECT COUNT(*) FROM well_observations WHERE day = ?", (day,)
            ).fetchone()[0]
            assert rows == len(WELLS)

    def test_photo_import_from_a_worker_thread(self, project, tmp_path):
        """The path taken once photo import moved off the GUI thread."""
        from PIL import Image

        sources = []
        for i in range(4):
            path = tmp_path / f"src{i}.png"
            Image.new("RGB", (12, 12), (i * 40, 90, 90)).save(path)
            sources.append(str(path))

        def importer(well, source):
            def run():
                assert project.add_photo_to_well(1, 1, well, source) is not None
            return run

        _run_concurrently([importer(f"A{i + 1}", s) for i, s in enumerate(sources)])
        assert project._conn.execute(
            "SELECT COUNT(*) FROM well_photos"
        ).fetchone()[0] == len(sources)
