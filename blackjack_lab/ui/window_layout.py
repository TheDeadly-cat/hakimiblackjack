"""Local display preferences only: never rules, observations or ledger state."""
import json
import sys
from pathlib import Path

from ..storage.safe_files import atomic_write


def work_area(root):
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        class MonitorInfo(ctypes.Structure):
            _fields_ = [('cbSize', wintypes.DWORD), ('monitor', wintypes.RECT),
                        ('work', wintypes.RECT), ('flags', wintypes.DWORD)]
        user = ctypes.windll.user32
        user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user.MonitorFromWindow.restype = wintypes.HANDLE
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        monitor = user.MonitorFromWindow(root.winfo_id(), 2)
        user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        if user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return info.work.left, info.work.top, info.work.right, info.work.bottom
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


class WindowLayout:
    defaults = {'compact': (720, 740), 'drawer': (720, 900), 'workbench': (1360, 900)}

    def __init__(self, root, db_path):
        self.root = root
        self.path = Path(str(Path(db_path).resolve()) + '.ui-preferences.json')
        self.sizes = {}
        self.cards_visible = True
        self.mode = None
        self.pending = None
        self.writable = True
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text(encoding='utf-8'))
                if (value.get('schema') != 1 or type(value.get('cards_visible')) is not bool
                        or not isinstance(value.get('sizes'), dict)):
                    raise ValueError('unknown preferences')
                for name, size in value['sizes'].items():
                    if name not in self.defaults or not isinstance(size, list) or len(size) != 2 or any(
                            type(n) is not int or not 200 <= n <= 16000 for n in size):
                        raise ValueError('invalid size')
                self.sizes = value['sizes']
                self.cards_visible = value['cards_visible']
            except (OSError, ValueError, TypeError, AttributeError):
                self.writable = False  # Keep malformed/unknown original preferences intact.
        self.binding = root.bind('<Configure>', self._configure, add='+')

    def _configure(self, event):
        if event.widget != self.root:
            return
        if self.pending:
            self.root.after_cancel(self.pending)
        self.pending = self.root.after(250, self.save)

    def remember(self):
        self.root.update_idletasks()
        if self.mode and self.root.state() == 'normal' and self.root.winfo_width() > 200:
            self.sizes[self.mode] = [self.root.winfo_width(), self.root.winfo_height()]

    def switch(self, mode):
        if mode == self.mode:
            return
        self.remember()
        old = self.mode
        self.mode = mode
        left, top, right, bottom = work_area(self.root)
        max_w, max_h = max(200, right - left - 16), max(200, bottom - top - 48)
        self.root.minsize(min(660, max_w), min(460, max_h))
        if self.root.state() == 'zoomed':
            return
        default = self.defaults[mode]
        if mode == 'drawer' and old == 'compact' and old in self.sizes:
            default = (self.sizes[old][0], self.sizes[old][1] + 160)
        width, height = self.sizes.get(mode, default)
        width, height = min(max_w, max(660, width)), min(max_h, max(460, height))
        x = min(max(left, self.root.winfo_x()), right - width - 8)
        y = min(max(top, self.root.winfo_y()), bottom - height - 40)
        self.root.geometry(f'{width}x{height}+{x}+{y}')

    def save(self):
        if self.pending:
            self.root.after_cancel(self.pending)
        self.pending = None
        self.remember()
        if self.writable:
            try:
                atomic_write(self.path, json.dumps(dict(schema=1, sizes=self.sizes,
                             cards_visible=self.cards_visible), ensure_ascii=False).encode('utf-8'))
            except OSError:
                pass  # A display preference failure must not interrupt recording.

    def close(self):
        if self.pending:
            self.root.after_cancel(self.pending)
        self.save()
        self.root.unbind('<Configure>', self.binding)
