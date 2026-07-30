import os
import logging
from collections import OrderedDict
from typing import Dict, List, Any

import pandas as pd
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QFileDialog, QTreeWidget, QTreeWidgetItem, QSplitter,
                             QScrollArea, QTabWidget, QMessageBox,
                             QTableWidget, QTableWidgetItem, QHeaderView,
                             QAbstractItemView, QMenu)
from PySide6.QtCore import Qt, Signal, QEvent

from PySide6.QtGui import QPixmap

from src.core.project_manager import ProjectManager
from src.core.task_manager import TaskManager
from src.core.utils import create_themed_icon
from src.ui.typography import scaled_pt, scaled_font

log = logging.getLogger(__name__)


class DayDocumentationWidget(QWidget):
    """A widget that contains all the documentation tools for a single day."""
    def __init__(self, manager: ProjectManager, day: int, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.day_to_document = day
        self.suggestions: Dict[str, Any] = {}
        self._alive = True
        self._worker = None
        #: (plate, well) pairs currently listed in the candidate-well table.
        self._wells: List[tuple] = []
        self._thumb_cache: "OrderedDict[str, QPixmap]" = OrderedDict()
        self._init_ui()
        self.refresh_suggestions()

    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        self.splitter = QSplitter(Qt.Horizontal)
        
        # Left Panel: Suggestions Checklist
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(QLabel("<b>Documentation Suggestions:</b>"))
        self.suggestion_tree = QTreeWidget()
        self.suggestion_tree.setHeaderHidden(True)
        self.suggestion_tree.currentItemChanged.connect(self._on_suggestion_selected)
        left_layout.addWidget(self.suggestion_tree)
        
        # Right Panel: Details and Actions
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setAlignment(Qt.AlignTop)
        
        self.suggestion_details_label = QLabel("Select a suggestion from the list.")
        font_details = self.font(); font_details.setPointSizeF(scaled_pt(12))
        self.suggestion_details_label.setFont(font_details)
        self.suggestion_details_label.setWordWrap(True)

        # One table with a single action, rather than a stack of rows each
        # carrying its own full-width button — a group of twenty candidate wells
        # produced twenty identical buttons.
        wells_header = QHBoxLayout()
        wells_header.setContentsMargins(0, 0, 0, 0)
        wells_header.addWidget(QLabel("<b>Candidate Wells:</b>"))
        wells_header.addStretch()
        self.attach_btn = QPushButton("Attach Photo…")
        self.attach_btn.setObjectName("PrimaryButton")
        self.attach_btn.setToolTip(
            "Import one or more images for the selected well. You can also "
            "double-click a row, or drop image files onto it."
        )
        self.attach_btn.setEnabled(False)
        self.attach_btn.clicked.connect(self._attach_to_selected_well)
        wells_header.addWidget(self.attach_btn)
        self.wells_header_widget = QWidget()
        self.wells_header_widget.setLayout(wells_header)

        self.wells_table = QTableWidget(0, 2)
        self.wells_table.setHorizontalHeaderLabels(["Well", "Photos"])
        self.wells_table.verticalHeader().setVisible(False)
        self.wells_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        # Single selection deliberately: one import goes to one well, because a
        # photograph filed against a well it does not show is a false record.
        self.wells_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.wells_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.wells_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.wells_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.wells_table.itemSelectionChanged.connect(self._on_well_selection_changed)
        self.wells_table.itemDoubleClicked.connect(
            lambda item: self._attach_to_row(item.row())
        )
        self.wells_table.setAcceptDrops(True)
        self.wells_table.viewport().setAcceptDrops(True)
        self.wells_table.setDragDropMode(QAbstractItemView.DropOnly)
        self.wells_table.viewport().installEventFilter(self)

        self.gallery_scroll_area = QScrollArea()
        self.gallery_scroll_area.setWidgetResizable(True)
        # Content is always a single row of fixed-size (150 px) thumbnails —
        # that row height never varies with how many photos are attached, so
        # a fixed height (not a cap) is the correct sizing here. A maximum
        # alone left this box at the mercy of the wells ↔ gallery space
        # negotiation above: with the wells table stretch=1 and unbounded, the
        # layout squeezed the gallery down to a fraction of its needed height
        # rather than granting it the maximum. The thumbnails no longer carry a
        # button underneath, so this is 30 px lower than it was.
        self.gallery_scroll_area.setFixedHeight(190)
        self.gallery_widget = QWidget()
        self.gallery_layout = QHBoxLayout(self.gallery_widget)
        self.gallery_scroll_area.setWidget(self.gallery_widget)

        right_layout.addWidget(self.suggestion_details_label)
        right_layout.addWidget(self.wells_header_widget)
        # Stretch goes to the well table, not the gallery: the number of
        # candidate wells varies and benefits from the room, while the gallery
        # is capped above.
        right_layout.addWidget(self.wells_table, 1)
        self.drop_hint_label = QLabel(
            "Select a well and choose Attach Photo, double-click a row, "
            "or drop image files onto it."
        )
        self.drop_hint_label.setObjectName("HintLabel")
        self.drop_hint_label.setWordWrap(True)
        right_layout.addWidget(self.drop_hint_label)
        right_layout.addWidget(QLabel("<b>Attached Photos:</b>"))
        right_layout.addWidget(self.gallery_scroll_area)
        
        self.splitter.addWidget(left_panel)
        self.splitter.addWidget(right_panel)
        self.splitter.setSizes([400, 600])
        
        self.no_data_label = QLabel(
            "No data recorded for this day.\n\nPlease enter observations in the 'Experiment View' to get documentation suggestions."
        )
        self.no_data_label.setAlignment(Qt.AlignCenter)
        font_no_data = self.font(); font_no_data.setPointSizeF(scaled_pt(14))
        self.no_data_label.setFont(font_no_data)
        
        main_layout.addWidget(self.no_data_label)
        main_layout.addWidget(self.splitter)
        self.splitter.hide()

    def refresh_suggestions(self):
        if self._worker is not None:
            try:
                self._worker.signals.result.disconnect(self._on_suggestions_computed)
            except (TypeError, RuntimeError):
                pass
        self._worker = TaskManager.instance().submit(self._compute_suggestions)
        self._worker.signals.result.connect(self._on_suggestions_computed)
        self._worker.signals.error.connect(self._on_suggestions_failed)

    def _on_suggestions_failed(self, message: str) -> None:
        """Surface a suggestion failure instead of leaving an empty panel.

        Without this the page reads "No data recorded for this day", which is
        indistinguishable from a day that genuinely has no observations.
        """
        self._worker = None
        if not self._alive:
            return
        log.error(f"Photo suggestions could not be computed: {message}")
        self.suggestions = {}
        self.suggestion_tree.clear()
        item = QTreeWidgetItem(self.suggestion_tree,
                               ["Suggestions unavailable — see the log for details."])
        item.setDisabled(True)
        # The tree lives in the splitter, which is hidden whenever there are no
        # suggestions; the message has to be on the visible side of that.
        self.no_data_label.hide()
        self.splitter.show()

    def _compute_suggestions(self) -> Dict[str, Any]:
        """Pure data computation — runs in the thread pool, no UI calls."""
        all_well_data = self.manager.get_well_observations_for_day(self.day_to_document)
        suggestions: Dict[str, Any] = {}

        if not all_well_data:
            return suggestions

        records = [{"plate": int(p), "well": w, **d} for p, wells in all_well_data.items() for w, d in wells.items()]
        df = pd.DataFrame(records)

        if 'status' not in df.columns:
            return suggestions

        layout_map = self.manager.get_all_plate_layouts()
        def get_conc_id(row): return layout_map.get(str(row['plate']), {}).get(row['well'])
        df['conc_id'] = df.apply(get_conc_id, axis=1)

        all_conc_groups = self.manager.get_concentrations()

        for conc_info in all_conc_groups:
            conc_id = conc_info['id']
            group_df = df[df['conc_id'] == conc_id].copy()
            if group_df.empty:
                continue

            conc_suggestions = []

            if 'sublethal_conditions' in group_df.columns:
                sublethal_df = group_df.explode('sublethal_conditions').dropna(subset=['sublethal_conditions'])
                if not sublethal_df.empty:
                    for condition in sorted(sublethal_df['sublethal_conditions'].unique()):
                        wells_with_plates = sorted(set(zip(
                            sublethal_df[sublethal_df['sublethal_conditions'] == condition]['plate'],
                            sublethal_df[sublethal_df['sublethal_conditions'] == condition]['well'],
                        )))
                        conc_suggestions.append({"type": "Malformation", "name": condition, "wells": wells_with_plates, "percent": 0})

            if 'status' in group_df.columns and not group_df['status'].dropna().empty:
                status_counts = group_df['status'].value_counts(normalize=True).sort_values(ascending=False)
                is_first = True
                for status, percentage in status_counts.items():
                    if pd.isna(status):
                        continue
                    wells_with_plates = sorted(set(zip(
                        group_df[group_df['status'] == status]['plate'],
                        group_df[group_df['status'] == status]['well'],
                    )))
                    if is_first:
                        conc_suggestions.append({"type": "Representative Status", "name": status, "wells": wells_with_plates, "percent": percentage})
                        is_first = False
                    elif percentage >= 0.10:
                        conc_suggestions.append({"type": "Significant Status", "name": status, "wells": wells_with_plates, "percent": percentage})

            if conc_suggestions:
                suggestions[conc_id] = conc_suggestions

        return suggestions

    def shutdown(self) -> None:
        """Detach from in-flight pool tasks before the project database closes.

        Photo import and suggestion computation both run in the thread pool
        against this project's manager, so their results must stop reaching a
        widget whose project is being torn down.
        """
        self._alive = False
        if self._worker is not None:
            try:
                self._worker.signals.result.disconnect(self._on_suggestions_computed)
            except (TypeError, RuntimeError):
                pass

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def _on_suggestions_computed(self, suggestions: Dict[str, Any]):
        # Released once delivered: a worker kept past its result leaves shutdown
        # and refresh disconnecting a signal that has already fired, which PySide
        # reports as a RuntimeWarning rather than the exception they guard for.
        self._worker = None
        if not self._alive:
            return
        self.suggestions = suggestions
        self._populate_suggestion_tree()
        if not self.suggestions:
            self.no_data_label.show()
            self.splitter.hide()
        else:
            self.no_data_label.hide()
            self.splitter.show()

    def _populate_suggestion_tree(self):
        self.suggestion_tree.clear()
        for conc_id, suggestions in sorted(self.suggestions.items()):
            conc_item = QTreeWidgetItem(self.suggestion_tree, [conc_id])
            conc_item.setFont(0, scaled_font(11, bold=True))
            
            suggestions.sort(key=lambda s: (s['type'] != 'Representative Status', s['type'] != 'Significant Status', s['name']))

            for sugg in suggestions:
                is_complete = any(
                    self.manager.get_photos_for_well(self.day_to_document, p_idx, w)
                    for p_idx, w in sugg['wells']
                )
                
                label = f"{sugg['type']}: {sugg['name']}"
                if sugg['percent'] > 0:
                    label += f" ({sugg['percent']:.1%})"

                sugg_item = QTreeWidgetItem(conc_item, [label])
                sugg_item.setData(0, Qt.UserRole, sugg)
                sugg_item.setIcon(0, create_themed_icon("check" if is_complete else "eye"))
        self.suggestion_tree.expandAll()

    def _on_suggestion_selected(self, current, previous):
        if not current or not current.data(0, Qt.UserRole):
            self.suggestion_details_label.setText("Select a suggestion from the list.")
            self._rebuild_wells_list([])
            self._update_gallery()
            return

        sugg_data = current.data(0, Qt.UserRole)
        group_name = current.parent().text(0) if current.parent() else "General"

        details_text = f"<b>Suggestion:</b> Document '{sugg_data['name']}' for group {group_name}."
        if sugg_data['percent'] > 0:
            details_text += f"<br>This status was observed in <b>{len(sugg_data['wells'])} wells</b>, representing <b>{sugg_data['percent']:.1%}</b> of this group."

        self.suggestion_details_label.setText(details_text)
        self._rebuild_wells_list(sugg_data['wells'])
        self._update_gallery()
        
    def _update_gallery(self):
        # Clear previous gallery content
        while self.gallery_layout.count():
            item = self.gallery_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        current_item = self.suggestion_tree.currentItem()
        if not current_item or not current_item.data(0, Qt.UserRole): return
        
        sugg_data = current_item.data(0, Qt.UserRole)
        photo_paths = []
        for plate_idx, well_id in sugg_data['wells']:
            relative_paths = self.manager.get_photos_for_well(self.day_to_document, plate_idx, well_id)
            for rel_path in relative_paths:
                photo_paths.append(self.manager.get_full_photo_path(rel_path))

        for path in sorted(list(set(photo_paths))):
            if os.path.exists(path):
                # Removal lives on a context menu rather than a button under each
                # thumbnail: a well with a dozen photographs was a wall of
                # identical Remove buttons.
                thumb_label = QLabel()
                thumb_label.setPixmap(self._cached_thumbnail(path))
                thumb_label.setToolTip(f"{os.path.basename(path)}\nRight-click to remove.")
                thumb_label.setContextMenuPolicy(Qt.CustomContextMenu)
                thumb_label.customContextMenuRequested.connect(
                    lambda pos, p=path, w=thumb_label: self._show_photo_menu(w, pos, p)
                )
                self.gallery_layout.addWidget(thumb_label)

        self.gallery_layout.addStretch()

    def _show_photo_menu(self, thumbnail: QLabel, pos, photo_path: str) -> None:
        """Context menu for one gallery thumbnail."""
        menu = QMenu(thumbnail)
        remove = menu.addAction("Remove Photo")
        remove.setToolTip("Remove this photo from the project and delete its file.")
        if menu.exec(thumbnail.mapToGlobal(pos)) is remove:
            self._remove_photo(photo_path)

    #: Decoded thumbnails retained at once. A four-day project can attach
    #: hundreds of images, and every one held here is a full decoded pixmap.
    _THUMB_CACHE_LIMIT = 120

    def _cached_thumbnail(self, path: str) -> QPixmap:
        """Scaled thumbnail for *path*, decoding at most once while it stays cached."""
        cached = self._thumb_cache.get(path)
        if cached is not None:
            self._thumb_cache.move_to_end(path)
            return cached
        pixmap = QPixmap(path).scaled(
            150, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self._thumb_cache[path] = pixmap
        if len(self._thumb_cache) > self._THUMB_CACHE_LIMIT:
            self._thumb_cache.popitem(last=False)
        return pixmap

    def _rebuild_wells_list(self, wells):
        """Fill the candidate-well table, one row per well.

        The photo count is the progress signal the old row list never gave: the
        operator could not tell a documented well from an undocumented one
        without selecting it.
        """
        self._wells = list(wells)
        self.wells_table.clearSelection()
        self.wells_table.setRowCount(len(self._wells))
        for row, (plate_idx, well_id) in enumerate(self._wells):
            count = len(self.manager.get_photos_for_well(
                self.day_to_document, plate_idx, well_id
            ))
            well_item = QTableWidgetItem(f"Plate {plate_idx} · {well_id}")
            well_item.setData(Qt.UserRole, (plate_idx, well_id))
            count_item = QTableWidgetItem(str(count) if count else "—")
            count_item.setTextAlignment(Qt.AlignCenter)
            if count:
                count_item.setToolTip(
                    f"{count} photograph{'s' if count != 1 else ''} attached to this well."
                )
            self.wells_table.setItem(row, 0, well_item)
            self.wells_table.setItem(row, 1, count_item)
        self._on_well_selection_changed()

    def _selected_well(self):
        """The (plate, well) pair for the selected row, or None."""
        rows = self.wells_table.selectionModel().selectedRows() if self.wells_table.selectionModel() else []
        if not rows:
            return None
        return self._well_at_row(rows[0].row())

    def _well_at_row(self, row: int):
        item = self.wells_table.item(row, 0)
        return item.data(Qt.UserRole) if item is not None else None

    def _on_well_selection_changed(self) -> None:
        self.attach_btn.setEnabled(self._selected_well() is not None)

    def _attach_to_selected_well(self) -> None:
        well = self._selected_well()
        if well is not None:
            self._browse_for_photo(*well)

    def _attach_to_row(self, row: int) -> None:
        well = self._well_at_row(row)
        if well is not None:
            self._browse_for_photo(*well)

    #: Extensions accepted from a drop, matching the file dialog's filter.
    _IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff")

    def eventFilter(self, source, event):
        """Accept image files dropped onto a row of the candidate-well table.

        Handled here rather than by subclassing QTableWidget: the drop has to
        resolve to the row under the cursor, which is the view's business, and
        the import it starts is this widget's.
        """
        if source is self.wells_table.viewport():
            kind = event.type()
            if kind in (QEvent.DragEnter, QEvent.DragMove):
                if self._dropped_images(event.mimeData()):
                    event.acceptProposedAction()
                    return True
            elif kind == QEvent.Drop:
                paths = self._dropped_images(event.mimeData())
                row = self.wells_table.rowAt(int(event.position().toPoint().y()))
                well = self._well_at_row(row) if row >= 0 else None
                if paths and well is not None:
                    event.acceptProposedAction()
                    self.wells_table.selectRow(row)
                    self._import_photos(paths, *well)
                    return True
        return super().eventFilter(source, event)

    def _dropped_images(self, mime) -> List[str]:
        """Local image files carried by a drag, if any."""
        if mime is None or not mime.hasUrls():
            return []
        return [
            url.toLocalFile() for url in mime.urls()
            if url.isLocalFile() and url.toLocalFile().lower().endswith(self._IMAGE_SUFFIXES)
        ]

    def _browse_for_photo(self, plate_idx: int, well_id: str):
        file_paths, _ = QFileDialog.getOpenFileNames(self, "Select Photos", "", "Image Files (*.png *.jpg *.jpeg *.tif *.tiff)")
        if file_paths:
            self._import_photos(file_paths, plate_idx, well_id)

    def _import_photos(self, source_paths: List[str], plate_idx: int, well_id: str):
        """Copy and convert the selected images off the GUI thread.

        Each image is decoded, converted to RGB and re-encoded as JPEG, which for
        a batch of microscopy TIFFs takes long enough to freeze the window if it
        runs inline. The buttons are held disabled until the batch reports back so
        the same files cannot be queued twice.
        """
        self._set_attach_enabled(False)
        worker = TaskManager.instance().submit(
            self._copy_photos, list(source_paths), plate_idx, well_id
        )
        worker.signals.result.connect(self._on_photos_imported)
        worker.signals.error.connect(self._on_photo_import_failed)

    def _copy_photos(self, source_paths: List[str], plate_idx: int, well_id: str) -> int:
        """Runs in the thread pool. Returns how many images were stored."""
        return sum(
            1 for path in source_paths
            if self.manager.add_photo_to_well(self.day_to_document, plate_idx, well_id, path)
        )

    def _on_photos_imported(self, _added: int):
        if not self._alive:
            return
        self._set_attach_enabled(True)
        self._populate_suggestion_tree()
        self._update_gallery()

    def _on_photo_import_failed(self, message: str):
        if not self._alive:
            return
        self._set_attach_enabled(True)
        QMessageBox.critical(self, "Photo Import Failed", message)

    def _set_attach_enabled(self, enabled: bool):
        """Hold the import controls while a batch is in flight.

        The table goes with the button so the same files cannot be queued twice
        by double-clicking or dropping during the copy.
        """
        self.wells_table.setEnabled(enabled)
        self.attach_btn.setEnabled(enabled and self._selected_well() is not None)

    def _remove_photo(self, photo_path: str):
        self._thumb_cache.pop(photo_path, None)
        self.manager.remove_photo_by_path(photo_path)
        self._populate_suggestion_tree()
        self._update_gallery()

class PhotoAssistantWidget(QWidget):
    """ Main page for photo documentation, containing tabs for each day. """
    def __init__(self, manager: ProjectManager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self._day_widgets: dict = {}
        self._loading = False
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        self.day_tabs = QTabWidget()
        self.day_tabs.currentChanged.connect(self._ensure_day_loaded)
        main_layout.addWidget(self.day_tabs)

    def _ensure_day_loaded(self, index: int):
        if index < 0 or self._loading:
            return
        day_idx = index + 1
        if self._day_widgets.get(day_idx) is not None:
            return
        self._loading = True
        try:
            placeholder = self.day_tabs.widget(index)
            real_widget = DayDocumentationWidget(self.manager, day_idx)
            self.day_tabs.removeTab(index)
            self.day_tabs.insertTab(index, real_widget, f"Day {day_idx}")
            self._day_widgets[day_idx] = real_widget
            self.day_tabs.setCurrentIndex(index)
            if placeholder is not None:
                placeholder.deleteLater()
        finally:
            self._loading = False

    def shutdown(self) -> None:
        """Called by MainWindow before the project's database is closed."""
        for widget in self._day_widgets.values():
            if widget is not None:
                widget.shutdown()

    def refresh_view(self):
        current_tab = self.day_tabs.currentIndex()
        self._loading = True
        try:
            for widget in self._day_widgets.values():
                if widget is not None:
                    widget.shutdown()
            while self.day_tabs.count():
                w = self.day_tabs.widget(0)
                self.day_tabs.removeTab(0)
                if w is not None:
                    w.deleteLater()
            self._day_widgets.clear()
            num_days = self.manager.get_project_info().get("num_days", 1)
            for day_idx in range(1, num_days + 1):
                self.day_tabs.addTab(QWidget(), f"Day {day_idx}")
                self._day_widgets[day_idx] = None
        finally:
            self._loading = False
        target = current_tab if (0 <= current_tab < self.day_tabs.count()) else 0
        if self.day_tabs.count() > 0:
            self._ensure_day_loaded(target)