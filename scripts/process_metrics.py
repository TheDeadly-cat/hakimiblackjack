"""Sample this process and its descendants only; no optional runtime dependency."""
import ctypes
from ctypes import wintypes as W
import os
from time import perf_counter


class ProcessMetrics:
    def __init__(self):
        self.root = os.getpid()
        self.started = perf_counter()
        self.cpu = {}
        self.peak_working_set = self.peak_private = self.peak_processes = self.samples = 0
        self.available = os.name == 'nt'
        if self.available:
            self.api = ctypes.WinDLL('kernel32', use_last_error=True)
            self.mem = ctypes.WinDLL('psapi', use_last_error=True)
            self.api.CreateToolhelp32Snapshot.argtypes = (W.DWORD, W.DWORD)
            self.api.CreateToolhelp32Snapshot.restype = W.HANDLE
            self.api.OpenProcess.argtypes = (W.DWORD, W.BOOL, W.DWORD)
            self.api.OpenProcess.restype = W.HANDLE
            self.api.CloseHandle.argtypes = (W.HANDLE,)
            self.api.GetProcessTimes.argtypes = (W.HANDLE, *([ctypes.POINTER(W.FILETIME)] * 4))
            self.mem.GetProcessMemoryInfo.argtypes = (W.HANDLE, ctypes.c_void_p, W.DWORD)
            self.sample(initial=True)

    def descendants(self):
        class Entry(ctypes.Structure):
            _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('pid', W.DWORD),
                        ('heap', ctypes.c_size_t), ('module', W.DWORD), ('threads', W.DWORD),
                        ('parent', W.DWORD), ('priority', W.LONG), ('flags', W.DWORD),
                        ('exe', W.WCHAR * 260)]
        self.api.Process32FirstW.argtypes = (W.HANDLE, ctypes.POINTER(Entry))
        self.api.Process32NextW.argtypes = (W.HANDLE, ctypes.POINTER(Entry))
        handle = self.api.CreateToolhelp32Snapshot(2, 0)
        if handle == ctypes.c_void_p(-1).value:
            return {self.root}
        links = []
        try:
            entry = Entry(); entry.size = ctypes.sizeof(entry)
            valid = self.api.Process32FirstW(handle, ctypes.byref(entry))
            while valid:
                links.append((entry.pid, entry.parent))
                valid = self.api.Process32NextW(handle, ctypes.byref(entry))
        finally:
            self.api.CloseHandle(handle)
        owned = {self.root}
        while True:
            updated = owned | {pid for pid, parent in links if parent in owned}
            if updated == owned:
                return owned
            owned = updated

    def sample(self, initial=False):
        if not self.available:
            return
        class Memory(ctypes.Structure):
            _fields_ = [('cb', W.DWORD), ('faults', W.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('peak', 'working', 'peak_paged', 'paged',
                                                     'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile', 'private')]
        working = private = count = 0
        for pid in self.descendants():
            handle = self.api.OpenProcess(0x410, False, pid)
            if not handle:
                continue
            try:
                times = [W.FILETIME() for _ in range(4)]
                if self.api.GetProcessTimes(handle, *(ctypes.byref(v) for v in times)):
                    ticks = lambda v: (v.dwHighDateTime << 32) + v.dwLowDateTime
                    key = (pid, ticks(times[0]))
                    cpu = (ticks(times[2]) + ticks(times[3])) / 10_000_000
                    old = self.cpu.get(key, (cpu if initial else 0, 0))
                    self.cpu[key] = (old[0], max(old[1], cpu))
                memory = Memory(); memory.cb = ctypes.sizeof(memory)
                if self.mem.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
                    working += memory.working; private += memory.private; count += 1
            finally:
                self.api.CloseHandle(handle)
        self.samples += 1
        self.peak_working_set = max(self.peak_working_set, working)
        self.peak_private = max(self.peak_private, private)
        self.peak_processes = max(self.peak_processes, count)

    def summary(self):
        self.sample()
        wall = perf_counter() - self.started
        cpu = sum(max(0, end - start) for start, end in self.cpu.values())
        return dict(available=self.available, samples=self.samples, observed_cpu_seconds=cpu,
                    observed_cpu_percent_one_core=100 * cpu / wall if wall else None,
                    peak_tree_working_set_bytes=self.peak_working_set if self.available else None,
                    peak_tree_private_bytes=self.peak_private if self.available else None,
                    peak_processes=self.peak_processes,
                    note='Sampled own process tree; exited-between-samples CPU/memory may be missed; not system-wide usage')

    def running(self, pid):
        if not self.available:
            return None
        self.api.GetExitCodeProcess.argtypes = (W.HANDLE, ctypes.POINTER(W.DWORD))
        handle = self.api.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = W.DWORD()
            return bool(self.api.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259)
        finally:
            self.api.CloseHandle(handle)
