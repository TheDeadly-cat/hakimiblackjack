"""Keymap for the local recording surface only.

0=T, 1=A, 2–9 keep their pip value; one key is one card. Enter/Tab only move
the recording cursor. Numpad digits are accepted when NumLock produces KP_0–9;
NumLock-off navigation keys are never treated as ranks. Long-press repeats are
suppressed by tracking the physical key until release or focus loss — never by
ignoring an identical rank inside 100ms.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.cards import TEN_BUCKET

TEXT_WIDGETS = {"Entry", "TEntry", "TCombobox", "Text", "Spinbox", "TSpinbox"}
NAV_KEYSYMS = {
    "left", "right", "up", "down", "home", "end", "insert", "delete",
    "prior", "next", "begin", "num_lock", "clear",
}

RANK_KEYSYMS = {
    "1": "A", "2": "2", "3": "3", "4": "4", "5": "5",
    "6": "6", "7": "7", "8": "8", "9": "9", "0": TEN_BUCKET,
    "a": "A",
    "kp_1": "A", "kp_2": "2", "kp_3": "3", "kp_4": "4", "kp_5": "5",
    "kp_6": "6", "kp_7": "7", "kp_8": "8", "kp_9": "9", "kp_0": TEN_BUCKET,
}

HOLE_KEYSYMS = {"period", "kp_decimal", "decimalpoint", "kp_separator"}
NEXT_KEYSYMS = {"return", "kp_enter", "tab"}
PREV_KEYSYMS = {"iso_left_tab"}
HIT_KEYSYMS = {"plus", "kp_add"}
STAND_KEYSYMS = {"minus", "kp_subtract"}
DOUBLE_KEYSYMS = {"asterisk", "kp_multiply"}
SPLIT_KEYSYMS = {"slash", "kp_divide"}
PAUSE_KEYSYMS = {"space"}
UNDO_KEYSYMS = {"backspace"}

SHIFT = 0x0001
CONTROL = 0x0004

KIND_RANK = "rank"
KIND_HOLE = "hole"
KIND_NEXT = "next_target"
KIND_PREV = "prev_target"
KIND_JUMP = "jump_seat"
KIND_HIT = "hit"
KIND_STAND = "stand"
KIND_DOUBLE = "double"
KIND_SPLIT = "split"
KIND_PAUSE = "pause"
KIND_UNDO = "undo"


@dataclass(frozen=True)
class KeyCommand:
    kind: str
    rank: str | None = None
    seat_number: int | None = None  # 0 = dealer, 1–7 = player


def _norm(keysym: str) -> str:
    key = (keysym or "").lower()
    # Tk versions can report printable punctuation either as a character or
    # its symbolic name (for example '.' versus 'period' on Windows).
    return {".": "period", "+": "plus", "-": "minus", "*": "asterisk",
            "/": "slash", " ": "space"}.get(key, key)


def resolve(keysym: str, state: int = 0) -> KeyCommand | None:
    """Map a Tk keysym/state pair. Does not bind global OS hooks."""
    key = _norm(keysym)
    if key in NAV_KEYSYMS:
        return None
    control = bool(state & CONTROL)
    shift = bool(state & SHIFT)
    if control and key in {str(n) for n in range(0, 8)} | {f"kp_{n}" for n in range(0, 8)}:
        digit = int(key[-1])
        return KeyCommand(KIND_JUMP, seat_number=digit)
    if control and key == "z":
        return KeyCommand(KIND_UNDO)
    if control:
        return None
    if key in RANK_KEYSYMS:
        return KeyCommand(KIND_RANK, rank=RANK_KEYSYMS[key])
    if key in HOLE_KEYSYMS:
        return KeyCommand(KIND_HOLE)
    if key in PREV_KEYSYMS or (key in NEXT_KEYSYMS and shift):
        return KeyCommand(KIND_PREV)
    if key in NEXT_KEYSYMS:
        return KeyCommand(KIND_NEXT)
    if key in HIT_KEYSYMS:
        return KeyCommand(KIND_HIT)
    if key in STAND_KEYSYMS:
        return KeyCommand(KIND_STAND)
    if key in DOUBLE_KEYSYMS:
        return KeyCommand(KIND_DOUBLE)
    if key in SPLIT_KEYSYMS:
        return KeyCommand(KIND_SPLIT)
    if key in PAUSE_KEYSYMS:
        return KeyCommand(KIND_PAUSE)
    if key in UNDO_KEYSYMS:
        return KeyCommand(KIND_UNDO)
    return None


class RepeatGuard:
    """One command per physical press; focus loss clears latched keys."""

    def __init__(self):
        self.pressed: set[str] = set()

    def accept_press(self, keysym: str) -> bool:
        key = _norm(keysym)
        if not key or key in self.pressed:
            return False
        self.pressed.add(key)
        return True

    def release(self, keysym: str) -> None:
        self.pressed.discard(_norm(keysym))

    def clear(self) -> None:
        self.pressed.clear()


class ManualKeyBinder:
    def __init__(self, root, handler, *, is_recording_surface):
        self.root = root
        self.handler = handler
        self.is_recording_surface = is_recording_surface
        self.guard = RepeatGuard()
        self.tag = f"ManualRecording-{id(self)}"
        self._tag_commands = []
        for sequence, callback in (("<KeyPress>", self.on_press), ("<KeyRelease>", self.on_release)):
            self._tag_commands.append((sequence, root.bind_class(self.tag, sequence, callback)))
        self._map = root.bind("<Map>", self._on_map, add="+")
        self._focus = root.bind("<FocusOut>", self.on_focus_out, add="+")
        self._install(root)

    def _install(self, widget):
        if widget.winfo_toplevel() != self.root:
            return
        tags = widget.bindtags()
        if self.tag not in tags:
            widget.bindtags((self.tag, *tags))
        for child in widget.winfo_children():
            self._install(child)

    def _on_map(self, event):
        self._install(event.widget)

    def on_press(self, event):
        if not self.is_recording_surface(event):
            return None
        command = resolve(event.keysym, getattr(event, "state", 0))
        if command is None:
            return None
        if not self.guard.accept_press(event.keysym):
            return "break"
        self.handler(command)
        return "break"

    def on_release(self, event):
        self.guard.release(event.keysym)
        if self.is_recording_surface(event) and resolve(event.keysym, getattr(event, "state", 0)):
            return "break"
        return None

    def on_focus_out(self, _event):
        self.guard.clear()

    def close(self):
        def remove(widget):
            widget.bindtags(tuple(tag for tag in widget.bindtags() if tag != self.tag))
            for child in widget.winfo_children():
                remove(child)
        remove(self.root)
        for sequence, command in self._tag_commands:
            self.root.unbind_class(self.tag, sequence)
            self.root.deletecommand(command)
        if self._map:
            self.root.unbind("<Map>", self._map)
        if self._focus:
            self.root.unbind("<FocusOut>", self._focus)
