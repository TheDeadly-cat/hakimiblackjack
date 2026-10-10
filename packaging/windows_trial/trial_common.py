"""Standard-library helpers for the separate, per-user TRIAL installer.

The pinned Git tree identifies source content, not a publisher signature.
No function in this module imports or runs downloaded application code.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import zipfile

REPOSITORY = 'TheDeadly-cat/hakimiblackjack'
COMMIT = 'af69ddb3111b93e43bfbf01f4aa8c249be166b7a'
TREE = '8b8b8142803fa4d4e4f76abcfddbb011411428b9'
PACKAGE_ID = 'T1R2-af69ddb'
TITLE = 'Hakimi Blackjack 试用版 T1R2'
ARCHIVE_URL = f'https://codeload.github.com/{REPOSITORY}/zip/{COMMIT}'
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_FILES = 10000


def sha256_file(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def git_hash(kind: str, content: bytes) -> str:
    header = f'{kind} {len(content)}\0'.encode('ascii')
    return hashlib.sha1(header + content).hexdigest()


def safe_relative(name: str) -> str:
    """Reject traversal, aliases, alternate streams and unsafe Windows names."""
    if not isinstance(name, str) or not name or '\\' in name or '\0' in name:
        raise ValueError(f'不安全的文件路径：{name!r}')
    path = PurePosixPath(name)
    parts = name.split('/')
    if path.is_absolute() or any(part in ('', '.', '..') for part in parts):
        raise ValueError(f'不安全的文件路径：{name!r}')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                *(f'LPT{i}' for i in range(1, 10))}
    for part in parts:
        if (any(ord(c) < 32 or c in '<>:"|?*' for c in part)
                or part.endswith((' ', '.')) or part.upper().split('.')[0] in reserved
                or part.lower() == '.git'):
            raise ValueError(f'不安全的 Windows 文件名：{name!r}')
    return path.as_posix()


def tree_hash(files: dict[str, tuple[str, str]]) -> str:
    """Reconstruct a Git tree using relative paths, Git modes and blob hashes."""
    root: dict = {}
    aliases: dict[str, str] = {}
    for name, (mode, blob) in files.items():
        name = safe_relative(name)
        if mode not in ('100644', '100755') or len(blob) != 40:
            raise ValueError('源码含不支持的文件类型或摘要')
        bytes.fromhex(blob)
        parts = name.split('/')
        for i in range(1, len(parts) + 1):
            segment = '/'.join(parts[:i])
            previous = aliases.setdefault(segment.casefold(), segment)
            if previous != segment:
                raise ValueError('源码含大小写冲突，不能安全安装到 Windows')
        current = root
        for part in parts[:-1]:
            current = current.setdefault(part, {})
            if not isinstance(current, dict):
                raise ValueError('文件与目录路径冲突')
        if parts[-1] in current:
            raise ValueError('重复的源码路径')
        current[parts[-1]] = (mode, blob)

    def recurse(node: dict) -> str:
        chunks = []
        # Git compares directory names as though followed by '/'.
        for name, item in sorted(node.items(), key=lambda pair: (
                pair[0] + ('/' if isinstance(pair[1], dict) else '')).encode('utf-8')):
            mode, digest = ('40000', recurse(item)) if isinstance(item, dict) else item
            chunks.append(mode.encode('ascii') + b' ' + name.encode('utf-8') + b'\0' + bytes.fromhex(digest))
        return git_hash('tree', b''.join(chunks))
    return recurse(root)


def inspect_archive(archive: Path, expected_tree: str = TREE) -> dict:
    """Validate every file, then compare the complete source tree before extraction."""
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError('源码压缩包超过允许大小')
    files, members, contents, roots, raw_names = {}, {}, {}, set(), set()
    expanded = 0
    with zipfile.ZipFile(archive) as zf:
        if len(zf.infolist()) > MAX_FILES:
            raise ValueError('源码压缩包文件过多')
        for info in zf.infolist():
            trimmed = info.filename[:-1] if info.is_dir() else info.filename
            safe_relative(trimmed)
            pieces = trimmed.split('/')
            roots.add(pieces[0])
            if info.filename.casefold() in raw_names:
                raise ValueError('源码压缩包有重复或冲突路径')
            raw_names.add(info.filename.casefold())
            mode = info.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if info.is_dir():
                if kind not in (0, stat.S_IFDIR):
                    raise ValueError('目录类型异常')
                continue
            if kind not in (0, stat.S_IFREG):
                raise ValueError('源码不能包含链接、设备或特殊文件')
            if len(pieces) < 2:
                raise ValueError('压缩包缺少 GitHub 单层根目录')
            relative = safe_relative('/'.join(pieces[1:]))
            if relative in files:
                raise ValueError('重复文件')
            expanded += info.file_size
            if info.file_size > MAX_MEMBER_BYTES or expanded > MAX_EXPANDED_BYTES:
                raise ValueError('解压大小超过上限')
            if info.flag_bits & 1:
                raise ValueError('不支持加密源码包')
            data = zf.read(info)
            if len(data) != info.file_size:
                raise ValueError('源码大小不符')
            git_mode = '100755' if mode & 0o111 else '100644'
            blob = git_hash('blob', data)
            files[relative] = (git_mode, blob)
            members[relative] = info.filename
            contents[relative] = {'git_mode': git_mode, 'git_blob': blob,
                                  'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
    if len(roots) != 1 or not files:
        raise ValueError('源码压缩包必须包含唯一的非空根目录')
    actual = tree_hash(files)
    if actual != expected_tree:
        raise ValueError(f'源码树不匹配，拒绝安装。期望 {expected_tree}，实际 {actual}')
    return {'schema': 'hakimi-trial-source-manifest-v1', 'commit': COMMIT,
            'git_tree': actual, 'files': contents, '_members': members}


def extract_verified(archive: Path, destination: Path, manifest: dict) -> None:
    assert_no_links(destination.parent)
    destination.mkdir(parents=False, exist_ok=False)
    with zipfile.ZipFile(archive) as zf:
        for relative, item in manifest['files'].items():
            target = destination.joinpath(*safe_relative(relative).split('/'))
            target.parent.mkdir(parents=True, exist_ok=True)
            data = zf.read(manifest['_members'][relative])
            # Recheck so a changed ZIP cannot bypass the prior verification.
            if hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('验证后源码发生变化')
            with target.open('xb') as stream:
                stream.write(data)


def assert_no_links(path: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (hasattr(candidate, 'is_junction') and candidate.is_junction()):
            raise ValueError(f'拒绝写入或删除链接目录：{candidate}')


def verify_installed_source(root: Path, manifest: dict) -> None:
    if manifest.get('commit') != COMMIT or manifest.get('git_tree') != TREE:
        raise ValueError('安装清单与固定试用版不一致')
    entries = {}
    for relative, item in manifest['files'].items():
        path = root.joinpath(*safe_relative(relative).split('/'))
        assert_no_links(path)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError(f'源码发生变化，已停止启动：{relative}')
        blob = git_hash('blob', data)
        if blob != item['git_blob']:
            raise ValueError(f'源码 Git 摘要不符：{relative}')
        entries[relative] = (item['git_mode'], blob)
    if tree_hash(entries) != TREE:
        raise ValueError('安装文件清单不能还原固定源码树')
    # Generated bytecode and source-addressed native artifacts are allowed;
    # unexpected importable source in the product package is not.
    package = root / 'blackjack_lab'
    for path in package.rglob('*'):
        if path.is_symlink():
            raise ValueError('运行目录含额外链接')
        if path.is_file() and path.suffix.lower() in ('.py', '.pyw', '.pyd'):
            if path.relative_to(root).as_posix() not in manifest['files']:
                raise ValueError(f'运行目录含额外代码：{path.name}')


def write_json_new(path: Path, value: dict) -> None:
    assert_no_links(path.parent)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def load_install(root: Path, require_success: bool = True) -> dict:
    assert_no_links(root)
    value = json.loads((root / 'install.json').read_text(encoding='utf-8'))
    if value.get('package_id') != PACKAGE_ID or value.get('commit') != COMMIT:
        raise ValueError('此目录不是本试用安装实例')
    if Path(value['install_root']).resolve() != root.resolve():
        raise ValueError('安装目录被移动；请重新安装，不要移动虚拟环境')
    if require_success:
        success = root / 'INSTALL_SUCCESS.json'
        if not success.is_file():
            raise ValueError('安装没有完成，请查看安装日志；不能标为已可运行')
        receipt = json.loads(success.read_text(encoding='utf-8'))
        if (not isinstance(receipt, dict) or receipt.get('package_id') != PACKAGE_ID
                or receipt.get('commit') != COMMIT or receipt.get('local_preflight_passed') is not True):
            raise ValueError('安装成功回执无效，请保留日志并重新核对')
    return value


@contextmanager
def instance_lock(data_root: Path):
    """Use an OS-owned lock; a stale file by itself is not a running instance."""
    assert_no_links(data_root)
    data_root.mkdir(parents=True, exist_ok=True)
    lock_path = data_root / '.trial-instance.lock'
    assert_no_links(lock_path)
    with lock_path.open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        acquired = False
        try:
            if os.name == 'nt':
                import msvcrt
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as error:
                    raise RuntimeError('试用版仍在运行。请正常关闭并等待保存结束后再操作。') from error
            else:
                import fcntl
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    raise RuntimeError('试用版仍在运行') from error
            acquired = True
            yield
        finally:
            if acquired:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
