"""
test_photo_panel.py — The candidate-well table that replaced the button stack.

The panel used to build one full-width "Attach Photo..." button per candidate
well, so a group of twenty wells rendered twenty identical buttons and gave no
sign of which wells were already documented. These tests pin the replacement:
one table carrying the per-well photo count, and one action bound to the
selected row.
"""
import os

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from src.core.constants import STATUS_LIVE_EMBRYO
from src.core.project_manager import ProjectManager


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def manager(tmp_path):
    m = ProjectManager.create_new(str(tmp_path / "Photos"), {
        "project_name": "Photos", "num_days": 4, "num_plates": 1,
        "plate_format": "96-well",
        "concentration_settings": {"concentrations": [
            {"id": "C1", "type": "Substrate", "value": 10.0, "wells": 3, "color": "#E67E22"},
        ], "required_embryos": 3, "required_plates": 1},
    })
    m.commit_plate_layout({"1": {"A1": "C1", "A2": "C1", "A3": "C1"}})
    for well in ("A1", "A2", "A3"):
        m.save_well_data(day=1, plate_index=1, well_id=well, status=STATUS_LIVE_EMBRYO,
                         sublethal_conditions=["Yolk sac oedema"],
                         lethal_conditions=[], notes="")
    return m


@pytest.fixture
def panel(qapp, manager):
    from src.ui.widgets.photo_assistant_widget import DayDocumentationWidget

    widget = DayDocumentationWidget(manager, 1)
    widget.show()
    for _ in range(300):
        qapp.processEvents()
        if widget.suggestion_tree.topLevelItemCount():
            break
    # Select the first suggestion so the candidate-well table is populated.
    top = widget.suggestion_tree.topLevelItem(0)
    widget.suggestion_tree.setCurrentItem(top.child(0))
    qapp.processEvents()
    yield widget
    widget.shutdown()
    widget.close()


def _image(path) -> str:
    QPixmap(40, 40).save(str(path))
    return str(path)


class TestOneActionNotOnePerWell:
    def test_panel_has_a_single_button(self, panel):
        buttons = panel.findChildren(QPushButton)
        assert [b.text() for b in buttons] == ["Attach Photo…"]

    def test_button_follows_the_selection(self, panel, qapp):
        panel.wells_table.clearSelection()
        qapp.processEvents()
        assert not panel.attach_btn.isEnabled()

        panel.wells_table.selectRow(0)
        qapp.processEvents()
        assert panel.attach_btn.isEnabled()
        assert panel._selected_well() == (1, "A1")

    def test_selection_is_single_row(self, panel):
        """One import goes to one well: a photograph filed against a well it does
        not show is a false record."""
        from PySide6.QtWidgets import QAbstractItemView

        assert panel.wells_table.selectionMode() == QAbstractItemView.SingleSelection
        assert panel.wells_table.selectionBehavior() == QAbstractItemView.SelectRows


class TestWellTable:
    def test_lists_every_candidate_well(self, panel):
        rows = panel.wells_table.rowCount()
        assert rows == 3
        shown = [panel.wells_table.item(i, 0).text() for i in range(rows)]
        assert shown == ["Plate 1 · A1", "Plate 1 · A2", "Plate 1 · A3"]

    def test_carries_the_plate_and_well_as_data(self, panel):
        assert panel._well_at_row(0) == (1, "A1")
        assert panel._well_at_row(2) == (1, "A3")

    def test_photo_count_column_reports_progress(self, panel, manager, tmp_path, qapp):
        """The old row list gave no sign of which wells were already documented."""
        assert panel.wells_table.item(0, 1).text() == "—"

        manager.add_photo_to_well(1, 1, "A1", _image(tmp_path / "shot.png"))
        panel._rebuild_wells_list(panel._wells)
        qapp.processEvents()
        assert panel.wells_table.item(0, 1).text() == "1"
        assert panel.wells_table.item(1, 1).text() == "—"

    def test_table_is_read_only(self, panel):
        from PySide6.QtWidgets import QAbstractItemView

        assert panel.wells_table.editTriggers() == QAbstractItemView.NoEditTriggers


class TestImportLock:
    """The controls are held while a batch copies so the same files cannot be
    queued twice by double-clicking or dropping."""

    def test_lock_disables_both_routes(self, panel, qapp):
        panel.wells_table.selectRow(0)
        qapp.processEvents()

        panel._set_attach_enabled(False)
        assert not panel.attach_btn.isEnabled()
        assert not panel.wells_table.isEnabled()

        panel._set_attach_enabled(True)
        assert panel.attach_btn.isEnabled()
        assert panel.wells_table.isEnabled()

    def test_unlock_respects_an_empty_selection(self, panel, qapp):
        panel.wells_table.clearSelection()
        qapp.processEvents()
        panel._set_attach_enabled(True)
        assert not panel.attach_btn.isEnabled()


class TestDroppedFiles:
    def test_accepts_image_files_only(self, panel, tmp_path):
        from PySide6.QtCore import QMimeData, QUrl

        mime = QMimeData()
        mime.setUrls([
            QUrl.fromLocalFile(str(tmp_path / "a.png")),
            QUrl.fromLocalFile(str(tmp_path / "b.TIF")),
            QUrl.fromLocalFile(str(tmp_path / "notes.txt")),
        ])
        accepted = panel._dropped_images(mime)
        assert [os.path.basename(p) for p in accepted] == ["a.png", "b.TIF"]

    def test_ignores_a_drag_with_no_urls(self, panel):
        from PySide6.QtCore import QMimeData

        assert panel._dropped_images(QMimeData()) == []
        assert panel._dropped_images(None) == []


class TestGallery:
    def test_thumbnails_carry_a_menu_not_a_button(self, panel, manager, tmp_path, qapp):
        """A well with a dozen photographs was a wall of identical Remove buttons."""
        manager.add_photo_to_well(1, 1, "A1", _image(tmp_path / "shot.png"))
        panel._update_gallery()
        qapp.processEvents()

        assert not panel.gallery_widget.findChildren(QPushButton)
        thumbs = [l for l in panel.gallery_widget.findChildren(QLabel)
                  if l.pixmap() is not None and not l.pixmap().isNull()]
        assert thumbs
        assert thumbs[0].contextMenuPolicy() == Qt.CustomContextMenu
