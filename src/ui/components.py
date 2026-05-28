# ui_components.py
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout,
                             QWidget, QRubberBand, QLineEdit, QGroupBox)
from PySide6.QtCore import (Qt, Signal, QRect, QPoint, QPointF, QSize,
                          QPropertyAnimation, QEasingCurve, Property, QEvent, QTimer,
                          QParallelAnimationGroup)
from PySide6.QtGui import (QColor, QPainter, QBrush, QPen, QPixmap, QTransform, QValidator, QDoubleValidator, QIcon)
from typing import Optional, Dict

from src.core.project_manager import ProjectManager
from src.core.utils import resource_path

class FlexibleDoubleValidator(QDoubleValidator):
    """
    A custom QDoubleValidator that accepts both comma (,) and period (.)
    as decimal separators, converting commas to periods internally for validation.
    """
    def validate(self, input_str: str, pos: int) -> tuple:
        """
        Overrides the validation to standardize the decimal separator.

        Args:
            input_str (str): The string being validated.
            pos (int): The cursor position.

        Returns:
            tuple: The validation state (Invalid, Intermediate, Acceptable).
        """
        standardized_str = input_str.replace(',', '.', 1)
        return super().validate(standardized_str, pos)

class FloatingLabelLineEdit(QWidget):
    """
    A custom QLineEdit widget with a floating label animation. The widget's
    appearance, including its border, is controlled entirely by QSS for
    better performance and easier theming.
    """
    def __init__(self, placeholder_text: str, parent=None):
        """
        Initializes the FloatingLabelLineEdit.

        Args:
            placeholder_text (str): The text to be used as the placeholder/label.
            parent (QWidget, optional): The parent widget. Defaults to None.
        """
        super().__init__(parent)
        self.setMinimumHeight(60)
        self._placeholder_text = placeholder_text

        self.line_edit = QLineEdit(self)
        self.label = QLabel(self._placeholder_text, self)
        
        self.setObjectName("FloatingLabelLineEdit")
        self.label.setObjectName("FloatingLabel")
        
        self.label.setAttribute(Qt.WA_TransparentForMouseEvents)
        
        self._font_size_up = 9
        self._font_size_down = 11
        font = self.font()
        font.setPointSize(self._font_size_down)
        self.line_edit.setFont(font)
        self.label.setFont(font)

        self.line_edit.setParent(self)
        self.label.setParent(self)
        
        self._setup_animations()
        
        self.line_edit.textChanged.connect(self._on_text_changed)
        self.line_edit.installEventFilter(self)

    def _setup_animations(self):
        """Initializes the QPropertyAnimation objects for the label."""
        self._pos_up = QPoint(0, 0)
        self._pos_down = QPoint(0, 0)
        self.pos_anim = QPropertyAnimation(self.label, b"pos")
        self.pos_anim.setDuration(180)
        self.pos_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self.font_anim = QPropertyAnimation(self, b"label_font_size")
        self.font_anim.setDuration(180)
        self.font_anim.setEasingCurve(QEasingCurve.InOutCubic)

    def resizeEvent(self, event):
        """Recalculates positions and sizes when the widget is resized."""
        super().resizeEvent(event)
        h_padding = 8
        v_padding_up = 4
        
        label_height = self.label.fontMetrics().height()
        line_edit_y = label_height + v_padding_up
        # Use the line edit's minimum sizeHint so text never clips on Windows
        # (Segoe UI has taller font metrics than macOS San Francisco at the same pt size)
        min_le_h = self.line_edit.sizeHint().height()
        line_edit_height = max(min_le_h, self.height() - line_edit_y - 2)
        self.line_edit.setGeometry(h_padding, line_edit_y,
            self.width() - (2 * h_padding), line_edit_height)
        
        self._pos_up = QPoint(h_padding, v_padding_up)
        # Center the placeholder text vertically within the line_edit area
        y_centered = self.line_edit.y() + (line_edit_height - self.label.sizeHint().height()) // 2
        self._pos_down = QPoint(h_padding, y_centered)
        
        # Set initial position based on content
        if not self.line_edit.text() and not self.line_edit.hasFocus():
            self.label.move(self._pos_down)
        else:
            self.label.move(self._pos_up)

    def _animate_up(self):
        """Animates the label to the upper position."""
        if self.label.pos() == self._pos_up and self.label.font().pointSize() == self._font_size_up: return
        self.pos_anim.setEndValue(self._pos_up)
        self.font_anim.setEndValue(self._font_size_up)
        self.pos_anim.start()
        self.font_anim.start()
    
    def _animate_down(self):
        """Animates the label to the lower (placeholder) position."""
        self.pos_anim.setEndValue(self._pos_down)
        self.font_anim.setEndValue(self._font_size_down)
        self.pos_anim.start()
        self.font_anim.start()

    def eventFilter(self, watched, event):
        """Filters events to trigger animations on focus changes."""
        if watched == self.line_edit:
            if event.type() == QEvent.FocusIn:
                self._animate_up()
            elif event.type() == QEvent.FocusOut:
                if not self.line_edit.text():
                    self._animate_down()
        return super().eventFilter(watched, event)
    
    def _on_text_changed(self, text: str):
        """Triggers animation when text content changes."""
        if text and self.label.pos() != self._pos_up:
            self._animate_up()
        elif not text and not self.line_edit.hasFocus():
            self._animate_down()

    def _get_label_font_size(self) -> int:
        """Property getter for the label's font size."""
        return self.label.font().pointSize()

    def _set_label_font_size(self, size: int):
        """Property setter for the label's font size."""
        font = self.label.font()
        font.setPointSize(size)
        self.label.setFont(font)
    
    label_font_size = Property(int, _get_label_font_size, _set_label_font_size)
    
    def text(self) -> str:
        """Returns the text from the internal QLineEdit."""
        return self.line_edit.text()

    def setText(self, text: str):
        """Sets the text of the internal QLineEdit and updates the label."""
        self.line_edit.setText(text)
        QTimer.singleShot(0, self._check_label_position)

    def _check_label_position(self):
        """
        Instantly sets the label position based on content, without animation.
        Used for setting initial text.
        """
        if self.line_edit.text():
            if self.width() > 0: self.resizeEvent(None)
            self.label.move(self._pos_up)
            font = self.label.font(); font.setPointSize(self._font_size_up); self.label.setFont(font)
        else:
            if self.width() > 0: self.resizeEvent(None)
            self.label.move(self._pos_down)
            font = self.label.font(); font.setPointSize(self._font_size_down); self.label.setFont(font)

    def setValidator(self, validator: QValidator):
        """Sets a validator for the internal QLineEdit."""
        self.line_edit.setValidator(validator)

class WellWidget(QFrame):
    """
    A custom widget representing a single well in a plate. It handles its own
    painting to appear as a circle and manages its visual state.
    """
    clicked = Signal(str, int, int)
    mouse_press = Signal(str, int)
    mouse_release = Signal(str, int)
    mouse_enter = Signal(str, int)
    
    def __init__(self, well_id: str, plate_index: int, day_index: int, parent=None):
        super().__init__(parent)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_StyledBackground, False)

        self.well_id = well_id; self.plate_index = plate_index; self.day_index = day_index
        self.current_status = "Live Embryo"
        self.base_color = QColor("#343a45"); self.is_selected = False
        self.is_hovered = False; self.is_assigned = False
        self.setMinimumSize(40, 40); self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("WellWidget")
        
        self.info_label = QLabel(self)
        self.info_label.setAlignment(Qt.AlignCenter); self.info_label.setWordWrap(True)
        self.info_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.info_label.setStyleSheet("background: transparent; border: none;")

        self.alert_icon = QLabel(self)
        self.alert_icon.setPixmap(QIcon(resource_path("resources/icons/triangle-alert.svg")).pixmap(QSize(16, 16)))
        self.alert_icon.setAlignment(Qt.AlignTop | Qt.AlignRight)
        self.alert_icon.setAttribute(Qt.WA_TransparentForMouseEvents); self.alert_icon.hide()
        self._update_text_content()

    def _update_text_content(self):
        """Updates the text and color of the well's label based on its state."""
        is_light = self.base_color.lightness() > 128 and self.is_assigned
        text_color = "black" if is_light else "white"
        _size = min(self.width(), self.height()) - 4
        _label_side = int(_size * 0.65)
        font_size = max(6, int(_label_side / 6))
        
        display_status = self.current_status
        if "Absent" in display_status: display_status = "Absent"
        
        rich_text = (f"<div style='color:{text_color}; line-height: 120%;'><b style='font-size:{font_size+1}pt;'>{self.well_id}</b><br>"
                     f"<span style='font-size:{font_size}pt;'>{display_status}</span></div>")
        self.info_label.setText(rich_text)
    
    def update_well_details(self, well_data: Dict):
        """
        Updates the well's visual status and tooltip based on the full data dictionary.

        Args:
            well_data (Dict): A dictionary containing 'status', 'notes', 
                              and 'sublethal_conditions' for the well.
        """
        self.update_status(well_data.get("status", "Live Embryo"))

        notes = well_data.get("notes", "").strip()
        sublethal = well_data.get("sublethal_conditions", [])
        
        tooltip_parts = []
        if sublethal:
            tooltip_parts.append(f"Conditions: {', '.join(sublethal)}")
        if notes:
            tooltip_parts.append(f"Notes: {notes}")
            
        if tooltip_parts:
            tooltip_text = "\n".join(tooltip_parts)
        else:
            tooltip_text = "No notes or conditions recorded for this well."
        
        self.setToolTip(tooltip_text)

    def set_concentration(self, color: QColor):
        """Sets the concentration color for the well."""
        self.is_assigned = color is not None
        self.setProperty("assigned", "true" if self.is_assigned else "false")
        self.base_color = color if color else QColor("#343a45")
        self._update_text_content(); self.update()

    def update_status(self, status: str):
        """Updates the main status text of the well."""
        self.current_status = status; self._update_text_content()

    def set_selected(self, selected: bool):
        """Sets the selection state of the well, triggering a repaint."""
        if self.is_selected != selected: self.is_selected = selected; self.update()

    def set_inconsistent(self, is_inconsistent: bool, tooltip_text: str = ""):
        """Shows or hides an alert icon for data inconsistencies."""
        self.alert_icon.setVisible(is_inconsistent)
        if is_inconsistent:
            current_tooltip = self.toolTip()
            new_tooltip = f"INCONSISTENCY:\n{tooltip_text}\n\n---\n\n{current_tooltip}"
            self.setToolTip(new_tooltip)

    def enterEvent(self, event):
        """Handles mouse hover enter event."""
        self.is_hovered = True; self.update()
        self.mouse_enter.emit(self.well_id, self.plate_index); super().enterEvent(event)

    def leaveEvent(self, event):
        """Handles mouse hover leave event."""
        self.is_hovered = False; self.update(); super().leaveEvent(event)

    def resizeEvent(self, event):
        """Repositions labels when the widget is resized."""
        size = min(self.width(), self.height()) - 4
        label_side = int(size * 0.65)
        center = self.rect().center()
        label_rect = QRect(0, 0, label_side, label_side)
        label_rect.moveCenter(center)
        self.info_label.setGeometry(label_rect)
        self.alert_icon.setGeometry(self.rect().adjusted(0, 4, -4, 0))
        self._update_text_content(); super().resizeEvent(event)

    def paintEvent(self, event):
        """Custom painting to draw the well as a circle with state indicators."""
        painter = QPainter(self)
        if self.is_assigned or self.is_selected:
            painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect(); size = min(rect.width(), rect.height()) - 4
        draw_rect = QRect(0, 0, size, size); draw_rect.moveCenter(rect.center())
        bg_color = self.base_color if self.is_assigned else QColor(73, 80, 87, 80)
        painter.setPen(Qt.NoPen); painter.setBrush(QBrush(bg_color)); painter.drawEllipse(draw_rect)
        if self.is_hovered and not self.is_selected:
            painter.setBrush(QBrush(QColor(255, 255, 255, 40))); painter.drawEllipse(draw_rect)
        pen = QPen()
        if self.is_selected: pen.setColor(QColor("#FFD700")); pen.setWidth(3)
        else:
            pen.setColor(QColor("#555") if self.is_assigned else QColor("#6c757d")); pen.setWidth(1)
            if not self.is_assigned: pen.setStyle(Qt.DashLine)
        painter.setBrush(Qt.NoBrush); painter.setPen(pen)
        border_rect = draw_rect.adjusted(pen.width()//2, pen.width()//2, -pen.width()//2, -pen.width()//2)
        painter.drawEllipse(border_rect)
    
    def mousePressEvent(self, event):
        """Emits signals when the well is pressed."""
        self.clicked.emit(self.well_id, self.plate_index, self.day_index)
        self.mouse_press.emit(self.well_id, self.plate_index); super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """Emits a signal when the mouse is released over the well."""
        self.mouse_release.emit(self.well_id, self.plate_index); super().mouseReleaseEvent(event)

class PlateWidget(QWidget):
    """
    A widget that displays a full 96-well plate grid, composed of
    individual WellWidget instances. It handles selection and interaction.
    """
    well_clicked = Signal(str, int, int)
    well_mouse_press = Signal(str, int)
    well_mouse_release = Signal(str, int)
    well_mouse_enter = Signal(str, int)
    wells_selected_for_painting = Signal(list, int)
    
    def __init__(self, plate_index: int, day_index: int, manager: ProjectManager,
                 rows: int = 8, cols: int = 12, parent=None):
        super().__init__(parent)
        self.plate_index = plate_index; self.day_index = day_index; self.manager = manager
        self.rows = rows; self.cols = cols
        self.well_widgets = {}; self._selected_well_id = None
        self.setMouseTracking(True)
        self.rubber_band = QRubberBand(QRubberBand.Rectangle, self)
        self.rubber_band.setStyleSheet("border: 2px dashed #00b4d8; background: rgba(0, 180, 216, 50);")
        self.origin = QPoint(); self.is_rubber_banding = False
        self._init_ui()
        self.reload_layout_from_manager()

    def _init_ui(self):
        """Initializes the grid of wells with row and column headers."""
        layout = QGridLayout(self)
        layout.setVerticalSpacing(2); layout.setHorizontalSpacing(6); layout.setContentsMargins(8, 8, 8, 8)
        for col in range(self.cols): layout.addWidget(QLabel(str(col + 1), alignment=Qt.AlignCenter), 0, col + 1)
        for row in range(self.rows): layout.addWidget(QLabel(chr(65 + row), alignment=Qt.AlignCenter), row + 1, 0)
        for row in range(self.rows):
            for col in range(self.cols):
                well_id = f"{chr(65 + row)}{col + 1}"
                well = WellWidget(well_id, self.plate_index, self.day_index)
                well.clicked.connect(self.well_clicked); well.mouse_press.connect(self.well_mouse_press)
                well.mouse_release.connect(self.well_mouse_release); well.mouse_enter.connect(self.well_mouse_enter)
                layout.addWidget(well, row + 1, col + 1); self.well_widgets[well_id] = well
        layout.setRowStretch(0, 1); layout.setColumnStretch(0, 1)
        for i in range(1, self.rows + 1): layout.setRowStretch(i, 5)
        for i in range(1, self.cols + 1): layout.setColumnStretch(i, 5)

    def set_selected_well(self, well_id: str):
        """Sets the currently selected well, updating the visual state."""
        if self._selected_well_id and self._selected_well_id in self.well_widgets:
            self.well_widgets[self._selected_well_id].set_selected(False)
        if well_id and well_id in self.well_widgets:
            self.well_widgets[well_id].set_selected(True); self._selected_well_id = well_id
        else: self._selected_well_id = None
    
    def reload_layout_from_manager(self):
        """ Reloads the plate's visual state from the main ProjectManager data. """
        layout_data = self.manager.get_plate_layout(self.plate_index)
        self.load_layout_from_data(layout_data)
        
    def load_layout_from_data(self, layout_data: dict):
        """ Updates the plate's visual state from a given layout dictionary. """
        conc_map = self.manager.get_concentration_map()
        day_obs = self.manager.get_well_observations_for_day(self.day_index)
        plate_data = day_obs.get(str(self.plate_index), {})

        for well_id, well_widget in self.well_widgets.items():
            conc_id = layout_data.get(well_id)
            conc_data = conc_map.get(conc_id)
            color = QColor(conc_data['color']) if conc_data else None
            well_widget.set_concentration(color)
            
            well_specific_data = plate_data.get(well_id, {})
            well_widget.update_well_details(well_specific_data)
            
            well_widget.set_selected(well_id == self._selected_well_id)

    def update_well_status(self, well_id: str, status: str):
        """Updates the visual status of a single well."""
        if well_id in self.well_widgets:
            well_data = self.manager.get_well_data(self.day_index, self.plate_index, well_id)
            self.well_widgets[well_id].update_well_details(well_data)

    def set_well_concentration(self, well_id: str, conc_id: Optional[str]) -> None:
        """Updates the concentration color of a single well without reloading the whole plate."""
        if well_id not in self.well_widgets:
            return
        conc_map = self.manager.get_concentration_map()
        conc_data = conc_map.get(conc_id) if conc_id else None
        color = QColor(conc_data['color']) if conc_data else None
        self.well_widgets[well_id].set_concentration(color)

    def mousePressEvent(self, event):
        """Handles mouse press to initiate rubber band selection."""
        if event.button() == Qt.LeftButton:
            self.origin = event.pos(); self.rubber_band.setGeometry(QRect(self.origin, QSize())); self.rubber_band.show(); self.is_rubber_banding = True
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Handles mouse move to resize the rubber band."""
        if self.is_rubber_banding: self.rubber_band.setGeometry(QRect(self.origin, event.pos()).normalized())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Handles mouse release to finalize rubber band selection."""
        if self.is_rubber_banding:
            self.rubber_band.hide(); self.is_rubber_banding = False
            selected_wells = [well_id for well_id, well_widget in self.well_widgets.items() if self.rubber_band.geometry().intersects(well_widget.geometry())]
            if selected_wells: self.wells_selected_for_painting.emit(selected_wells, self.plate_index)
        super().mouseReleaseEvent(event)

class CollapsibleBox(QGroupBox):
    """
    A custom QGroupBox that can be collapsed and expanded with an animation.
    The checkbox in the title bar controls the visibility of its content.
    """
    def __init__(self, title="", parent=None):
        super().__init__(title, parent)
        self.setCheckable(True)
        self.setChecked(True)

        self.animation = QParallelAnimationGroup()
        
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.content_widget)

        self.toggle_animation = QPropertyAnimation(self, b"maximumHeight")
        self.toggle_animation.setDuration(300)
        self.toggle_animation.setEasingCurve(QEasingCurve.InOutCubic)

        self.toggled.connect(self.on_toggled)
        
    def setContentLayout(self, layout):
        """
        Sets the layout or a single widget for the content area of the box.
        
        Args:
            layout: A QLayout or QWidget to be added to the content area.
        """
        # Clear existing layout
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)

        if isinstance(layout, QWidget):
            self.content_layout.addWidget(layout)
        else:
            # Move all items from the passed layout to the content layout
            while layout.count():
                item = layout.takeAt(0)
                self.content_layout.addItem(item)
                
        # Set initial state without animation
        self.on_toggled(self.isChecked(), immediate=True)

    def on_toggled(self, checked, immediate=False):
        """
        Handles the expand/collapse animation when the box is toggled.

        Args:
            checked (bool): The new checked state.
            immediate (bool): If True, sets the final state instantly without animation.
        """
        self.content_widget.setVisible(checked)
        
        start_height = self.height()
        
        if checked:
            end_height = self.sizeHint().height()
        else:
            end_height = self.titleBarHeight()

        if immediate:
            self.setMaximumHeight(end_height)
            return
            
        self.toggle_animation.setStartValue(start_height)
        self.toggle_animation.setEndValue(end_height)
        self.toggle_animation.start()

    def titleBarHeight(self) -> int:
        """Calculates the height of the QGroupBox title bar."""
        return self.fontMetrics().height() + 15


# ---------------------------------------------------------------------------
# Plate format selector widgets
# ---------------------------------------------------------------------------

class WellGridCanvas(QWidget):
    """Paints a miniature well grid for use inside PlateThumbnail."""

    def __init__(self, rows: int, cols: int, parent=None):
        super().__init__(parent)
        self._rows = rows
        self._cols = cols

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        m = 3
        cw = (w - 2 * m) / self._cols
        ch = (h - 2 * m) / self._rows
        r = min(cw, ch) * 0.32
        for row in range(self._rows):
            for col in range(self._cols):
                cx = m + (col + 0.5) * cw
                cy = m + (row + 0.5) * ch
                painter.setPen(QPen(QColor("#888"), 0.5))
                painter.setBrush(QBrush(QColor("#555")))
                painter.drawEllipse(QPointF(cx, cy), r, r)


class PlateThumbnail(QFrame):
    """A clickable card showing a mini well-grid preview and format label."""

    clicked = Signal()

    def __init__(self, fmt: str, rows: int, cols: int, parent=None):
        super().__init__(parent)
        self.setObjectName("PlateThumbnail")
        self.setFixedSize(80, 80)
        self.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 3)
        layout.setSpacing(2)
        self._canvas = WellGridCanvas(rows, cols)
        self._label = QLabel(fmt)
        self._label.setAlignment(Qt.AlignCenter)
        font = self._label.font()
        font.setPointSize(8)
        self._label.setFont(font)
        layout.addWidget(self._canvas, 1)
        layout.addWidget(self._label)
        self.set_selected(False)

    def set_selected(self, selected: bool):
        self.setProperty("plateSelected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class PlateFormatSelector(QWidget):
    """Horizontal row of PlateThumbnail cards for selecting a plate format."""

    format_changed = Signal(str)

    def __init__(self, initial_format: str = "96-well", parent=None):
        super().__init__(parent)
        self._selected = initial_format
        self._thumbnails: dict = {}
        self._init_ui()

    def _init_ui(self):
        from src.core.constants import PLATE_FORMATS
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for fmt, (rows, cols) in PLATE_FORMATS.items():
            thumb = PlateThumbnail(fmt, rows, cols)
            thumb.clicked.connect(lambda f=fmt: self._select(f))
            layout.addWidget(thumb)
            self._thumbnails[fmt] = thumb
        layout.addStretch()
        self._thumbnails[self._selected].set_selected(True)

    def _select(self, fmt: str):
        for f, thumb in self._thumbnails.items():
            thumb.set_selected(f == fmt)
        if fmt != self._selected:
            self._selected = fmt
            self.format_changed.emit(fmt)

    def set_format(self, fmt: str):
        """Silently update the highlighted thumbnail without emitting format_changed."""
        self._selected = fmt
        for f, thumb in self._thumbnails.items():
            thumb.set_selected(f == fmt)

    @property
    def selected_format(self) -> str:
        return self._selected


# ---------------------------------------------------------------------------
# Loading overlay (shared between hub and analysis pages)
# ---------------------------------------------------------------------------

class SpinningIcon(QWidget):
    """A widget that displays a pixmap and rotates it indefinitely."""
    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self._pixmap = pixmap
        self._rotation = 0.0
        self.setFixedSize(pixmap.size())

        self.animation = QPropertyAnimation(self, b"rotation", self)
        self.animation.setDuration(2000)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(360.0)
        self.animation.setLoopCount(-1)
        self.animation.setEasingCurve(QEasingCurve.Type.Linear)

    @Property(float)
    def rotation(self):
        return self._rotation

    @rotation.setter
    def rotation(self, value):
        self._rotation = value
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        center = self.rect().center()
        transform = (QTransform()
                     .translate(center.x(), center.y())
                     .rotate(self._rotation)
                     .translate(-center.x(), -center.y()))
        painter.setTransform(transform)
        painter.drawPixmap(self.rect(), self._pixmap)

    def start(self):
        self.animation.start()

    def stop(self):
        self.animation.stop()


class LoadingOverlay(QFrame):
    """An overlay that shows a spinning animation while work is in progress."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LoadingOverlay")
        self.setStyleSheet("#LoadingOverlay { background-color: rgba(0, 0, 0, 180); border-radius: 10px; }")

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(20)

        pixmap = QPixmap(resource_path("resources/icons/loading.png")).scaled(
            64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.spinner = SpinningIcon(pixmap, self)

        self.label = QLabel()
        self.label.setStyleSheet("color: white; background-color: transparent;")
        font = self.font()
        font.setPointSize(16)
        self.label.setFont(font)

        layout.addWidget(self.spinner)
        layout.addWidget(self.label)
        self.hide()

    def setText(self, text: str):
        self.label.setText(text)

    def show(self):
        if self.parentWidget():
            self.setGeometry(self.parentWidget().rect())
        self.spinner.start()
        super().show()
        self.raise_()

    def hide(self):
        self.spinner.stop()
        super().hide()