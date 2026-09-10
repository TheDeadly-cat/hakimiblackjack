"""Atomic local outputs, protecting active databases and keeping old files on failure."""
from collections import Counter
import os
from pathlib import Path
import tempfile

_databases = Counter()


def register_database(path):
    _databases[Path(path).resolve()] += 1


def unregister_database(path):
    path = Path(path).resolve()
    _databases[path] -= 1
    if _databases[path] <= 0:
        del _databases[path]


def check_output_path(path, protected_paths=()):
    path = Path(path).resolve()
    if path.suffix.lower() in (".db", ".sqlite", ".sqlite3") or path.name.endswith(("-wal", "-shm", "-journal")):
        raise ValueError("导出不能写入数据库或其日志文件")
    protected = set(_databases) | {Path(p).resolve() for p in protected_paths}
    for db in protected:
        if path == db or str(path) in {str(db) + suffix for suffix in ("-wal", "-shm", "-journal")}:
            raise ValueError("不能覆盖活动数据库")
        if path.exists() and db.exists() and os.path.samefile(path, db):
            raise ValueError("目标是活动数据库的别名，拒绝覆盖")
    if path.exists():
        if not path.is_file():
            raise ValueError("导出目标必须是文件")
        with path.open("rb") as stream:
            if stream.read(16) == b"SQLite format 3\x00":
                raise ValueError("目标是SQLite数据库，拒绝覆盖")
    return path


def _write_and_sync(fd, data):
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def atomic_write(path, data, *, overwrite=True, protected_paths=()):
    path = check_output_path(path, protected_paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".hakimi-output-", suffix=".tmp", dir=path.parent)
    temp = Path(temp)
    try:
        _write_and_sync(fd, data)
        check_output_path(path, protected_paths)
        if overwrite:
            os.replace(temp, path)
        else:
            # Atomic create-without-overwrite, including two concurrent result writers.
            os.link(temp, path)
        return path
    finally:
        # Cleanup failure must not turn an already published file into "unsaved".
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def spreadsheet_cell(value):
    text = "" if value is None else str(value)
    if text.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return value
