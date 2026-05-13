import os
import pandas as pd
import re
from typing import Dict, List, Any
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QFileDialog, QTreeWidget, QTreeWidgetItem, QSplitter,
                             QScrollArea, QFrame, QListWidget, QListWidgetItem, QTabWidget)
from PySide6.QtCore import Qt, QSize, Signal

from PySide6.QtGui import QFont, QPixmap, QIcon

from src.core.project_manager import ProjectManager
from src.core.task_manager import TaskManager
from src.core.utils import resource_path, create_icon

class DayDocumentationWidget(QWidget):
    """A widget that contains all the documentation tools for a single day."""
    def __init__(self, manager: ProjectManager, day: int, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.day_to_document = day
        self.suggestions: Dict[str, Any] = {}
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
        font_details = self.font(); font_details.setPointSize(12)
        self.suggestion_details_label.setFont(font_details)
        self.suggestion_details_label.setWordWrap(True)

        self.candidate_wells_list = QListWidget()
        self.candidate_wells_list.setToolTip("Select a well from this list before attaching a photo.")
        
        self.gallery_scroll_area = QScrollArea()
        self.gallery_scroll_area.setWidgetResizable(True)
        self.gallery_widget = QWidget() # Make gallery_widget a class attribute
        self.gallery_layout = QHBoxLayout(self.gallery_widget)
        self.gallery_scroll_area.setWidget(self.gallery_widget)

        self.attach_button = QPushButton("Attach Photo...")
        self.attach_button.setIcon(create_icon("book-image.svg"))
        self.attach_button.clicked.connect(self._browse_for_photo)
        self.attach_button.setEnabled(False)

        right_layout.addWidget(self.suggestion_details_label)
        right_layout.addWidget(QLabel("<b>Candidate Wells:</b>"))
        right_layout.addWidget(self.candidate_wells_list)
        right_layout.addWidget(QLabel("<b>Attached Photos:</b>"))
        right_layout.addWidget(self.gallery_scroll_area, 1)
        right_layout.addWidget(self.attach_button)
        
        self.splitter.addWidget(left_panel)
        self.splitter.addWidget(right_panel)
        self.splitter.setSizes([400, 600])
        
        self.no_data_label = QLabel(
            "No data recorded for this day.\n\nPlease enter observations in the 'Experiment View' to get documentation suggestions."
        )
        self.no_data_label.setAlignment(Qt.AlignCenter)
        font_no_data = self.font(); font_no_data.setPointSize(14)
        self.no_data_label.setFont(font_no_data)
        
        main_layout.addWidget(self.no_data_label)
        main_layout.addWidget(self.splitter)
        self.splitter.hide()

    def refresh_suggestions(self):
        """Generates suggestions in the thread pool, then populates the tree on the main thread."""
        worker = TaskManager.instance().submit(self._compute_suggestions)
        worker.signals.result.connect(self._on_suggestions_computed)

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

    def _on_suggestions_computed(self, suggestions: Dict[str, Any]):
        """Called on the main thread when suggestion computation finishes."""
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
        self.attach_button.setEnabled(False)
        for conc_id, suggestions in sorted(self.suggestions.items()):
            conc_item = QTreeWidgetItem(self.suggestion_tree, [conc_id])
            conc_item.setFont(0, QFont("Inter", 11, QFont.Bold))
            
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
                sugg_item.setIcon(0, create_icon("check.svg" if is_complete else "eye-dark.svg"))
        self.suggestion_tree.expandAll()

    def _on_suggestion_selected(self, current, previous):
        if not current or not current.data(0, Qt.UserRole):
            self.suggestion_details_label.setText("Select a suggestion from the list.")
            self.candidate_wells_list.clear()
            self.attach_button.setEnabled(False)
            self._update_gallery()
            return
        
        sugg_data = current.data(0, Qt.UserRole)
        group_name = current.parent().text(0) if current.parent() else "General"
        
        details_text = f"<b>Suggestion:</b> Document '{sugg_data['name']}' for group {group_name}."
        if sugg_data['percent'] > 0:
             details_text += f"<br>This status was observed in <b>{len(sugg_data['wells'])} wells</b>, representing <b>{sugg_data['percent']:.1%}</b> of this group."

        self.suggestion_details_label.setText(details_text)
        self.candidate_wells_list.clear()
        for plate_idx, well_id in sugg_data['wells']:
            self.candidate_wells_list.addItem(f"Plate {plate_idx + 1} - {well_id}")
            
        self.attach_button.setEnabled(True)
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
                thumb_widget = QWidget()
                thumb_layout = QVBoxLayout(thumb_widget)
                thumb_layout.setContentsMargins(0, 0, 0, 0)
                
                pixmap = QPixmap(path)
                thumb_pixmap = pixmap.scaled(250, 250, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                
                thumb_label = QLabel()
                thumb_label.setPixmap(thumb_pixmap)
                thumb_label.setToolTip(f"Path: {path}")

                remove_btn = QPushButton("Remove")
                remove_btn.setToolTip(f"Remove this photo from the project (file will not be deleted).")
                remove_btn.clicked.connect(lambda checked, p=path: self._remove_photo(p))

                thumb_layout.addWidget(thumb_label)
                thumb_layout.addWidget(remove_btn)
                self.gallery_layout.addWidget(thumb_widget)
                
        self.gallery_layout.addStretch()

    def _browse_for_photo(self):
        current_well_item = self.candidate_wells_list.currentItem()
        if not current_well_item: return
        
        item_text = current_well_item.text()
        match = re.match(r"Plate (\d+) - (.*)", item_text)
        if not match: return
            
        plate_idx = int(match.group(1)) - 1
        well_id = match.group(2)
        
        file_paths, _ = QFileDialog.getOpenFileNames(self, "Select Photos", "", "Image Files (*.png *.jpg *.jpeg *.tif *.tiff)")
        if file_paths:
            for path in file_paths:
                self._add_photo(path, plate_idx, well_id)
    
    def _add_photo(self, source_path: str, plate_idx: int, well_id: str):
        self.manager.add_photo_to_well(self.day_to_document, plate_idx, well_id, source_path)
        self._populate_suggestion_tree()
        self._update_gallery()

    # Function to remove a photo reference from the project
    def _remove_photo(self, photo_path: str):
        """
        Removes a photo's reference from the project data and refreshes the UI.
        This requires a corresponding 'remove_photo' method in ProjectManager.
        """
        self.manager.remove_photo_by_path(photo_path) 
        self._populate_suggestion_tree()
        self._update_gallery()

class PhotoAssistantWidget(QWidget):
    """ Main page for photo documentation, containing tabs for each day. """
    def __init__(self, manager: ProjectManager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        self.day_tabs = QTabWidget()
        main_layout.addWidget(self.day_tabs)

    def refresh_view(self):
        current_tab = self.day_tabs.currentIndex()
        self.day_tabs.clear()
        num_days = self.manager.get_project_info().get("num_days", 1)
        for day_idx in range(1, num_days + 1):
            day_widget = DayDocumentationWidget(self.manager, day_idx)
            self.day_tabs.addTab(day_widget, f"Day {day_idx}")
        
        if current_tab != -1 and current_tab < self.day_tabs.count():
            self.day_tabs.setCurrentIndex(current_tab)