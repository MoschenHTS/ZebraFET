"""
commands.py — Undoable operations.

Scoring a plate is hundreds of individual clicks, each written to the database
the moment it is made. That is the right storage model — nothing is ever lost to
a crash — but it left no way back from a misclick except to remember what the
well said before and set it again by hand.

Both commands here are constructed *after* their change has already been applied,
because the editor and the layout page write through their own validated paths
and duplicating that logic inside a command would be a second place for the rules
to live. The first ``redo`` is therefore suppressed; every later one re-applies
normally.
"""
from typing import Any, Callable, Dict

from PySide6.QtGui import QUndoCommand

#: Shared by every well edit so QUndoStack will consider merging them.
_WELL_EDIT_ID = 1001
_PLATE_LAYOUT_ID = 1002

#: The fields an undo must restore. Photos and the auto_filled flag are excluded:
#: photos are managed separately, and auto_filled is set by the write itself.
WELL_STATE_FIELDS = ("status", "sublethal_conditions", "lethal_conditions", "notes")


def well_state(data: Dict[str, Any]) -> Dict[str, Any]:
    """The undoable part of a well observation, normalized for comparison."""
    return {
        "status": data.get("status", ""),
        "sublethal_conditions": sorted(data.get("sublethal_conditions") or []),
        "lethal_conditions": sorted(data.get("lethal_conditions") or []),
        "notes": data.get("notes", "") or "",
    }


class WellEditCommand(QUndoCommand):
    """One well's observation, before and after an edit."""

    def __init__(self, manager, day: int, plate: int, well: str,
                 before: Dict[str, Any], after: Dict[str, Any],
                 on_applied: Callable[[int, int, str], None]):
        super().__init__(f"score well {well} on day {day}")
        self._manager = manager
        self._key = (day, plate, well)
        self._before = well_state(before)
        self._after = well_state(after)
        self._on_applied = on_applied
        self._already_applied = True

    def id(self) -> int:
        return _WELL_EDIT_ID

    def mergeWith(self, other: QUndoCommand) -> bool:
        """Fold a further edit of the same well into this one.

        The editor saves on every checkbox toggle and on a debounce timer while
        notes are typed, so one visit to a well produces a dozen writes. Undo has
        to step back over the visit, not over each keystroke. Merging only ever
        applies to the command at the top of the stack, so moving to another well
        ends the run without needing a timer.
        """
        if other.id() != self.id() or getattr(other, "_key", None) != self._key:
            return False
        self._after = other._after
        return True

    def _write(self, state: Dict[str, Any]) -> None:
        day, plate, well = self._key
        self._manager.save_well_data(
            day=day, plate_index=plate, well_id=well,
            status=state["status"],
            sublethal_conditions=list(state["sublethal_conditions"]),
            lethal_conditions=list(state["lethal_conditions"]),
            notes=state["notes"],
            auto_filled=0,
        )
        self._on_applied(day, plate, well)

    def redo(self) -> None:
        # QUndoStack redoes on push, but the editor has already written this edit.
        if self._already_applied:
            self._already_applied = False
            return
        self._write(self._after)

    def undo(self) -> None:
        self._write(self._before)


class PlateLayoutCommand(QUndoCommand):
    """A wholesale change to the plate layout buffer, such as clearing a plate.

    The layout page edits an in-memory buffer that is committed separately, so
    this restores the buffer rather than the database; an undone change that was
    never saved simply never reaches it.
    """

    def __init__(self, page, before: Dict[str, Dict[str, str]],
                 after: Dict[str, Dict[str, str]], text: str):
        super().__init__(text)
        self._page = page
        self._before = before
        self._after = after
        self._already_applied = True

    def id(self) -> int:
        # Never merged: each is a deliberate, discrete action on a whole plate.
        return _PLATE_LAYOUT_ID

    def mergeWith(self, other: QUndoCommand) -> bool:
        return False

    def redo(self) -> None:
        if self._already_applied:
            self._already_applied = False
            return
        self._page.apply_layout_snapshot(self._after)

    def undo(self) -> None:
        self._page.apply_layout_snapshot(self._before)
