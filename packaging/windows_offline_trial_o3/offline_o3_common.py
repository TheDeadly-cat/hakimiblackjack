"""Identity and read-only verification for the isolated frozen trial."""
from pathlib import Path
import hashlib
import json
import os
import sys
import uuid

from trial_common import assert_no_links, git_hash, sha256_file, safe_relative, tree_hash
from offline_o3_identity import COMMIT, TREE, SOURCE_FILE_COUNT, PACKAGE_ID, TITLE

def verify_installed_source(root, manifest, *, verified_files=None):
    """Verify this complete fixed Git tree without changing online R4's globals."""
    if (manifest.get('commit') != COMMIT or manifest.get('git_tree') != TREE
            or len(manifest.get('files', {})) != SOURCE_FILE_COUNT):
        raise ValueError('安装清单与固定离线试用版不一致')
    entries = {}
    for relative, item in manifest['files'].items():
        path = root.joinpath(*safe_relative(relative).split('/'))
        assert_no_links(path)
        if verified_files is None:
            data = path.read_bytes()
            digest, blob = hashlib.sha256(data).hexdigest(), git_hash('blob', data)
        else:
            # Only the current verify_bundle call supplies these observations.
            # No persistent cache or timestamp-based trust is involved.
            if relative not in verified_files:
                raise ValueError('冻结清单缺少源码文件：' + relative)
            digest, blob = verified_files[relative]
        if digest != item['sha256']:
            raise ValueError('源码发生变化，已停止启动：' + relative)
        if blob != item['git_blob']:
            raise ValueError('源码 Git 摘要不符：' + relative)
        entries[relative] = item['git_mode'], blob
    if tree_hash(entries) != TREE:
        raise ValueError('安装文件清单不能还原固定源码树')
    for path in (root / 'blackjack_lab').rglob('*'):
        assert_no_links(path)
        if path.is_file() and path.suffix.lower() in ('.py', '.pyw', '.pyd'):
            if path.relative_to(root).as_posix() not in manifest['files']:
                raise ValueError('运行目录含额外代码：' + path.name)


def program_root():
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('此入口仅用于已冻结的试用产物。')
    return Path(sys.executable).resolve().parent


def verify_bundle(root):
    assert_no_links(root)
    manifest = json.loads((root / 'BUNDLE_MANIFEST.json').read_text(encoding='utf-8'))
    if (manifest.get('package_id') != PACKAGE_ID or manifest.get('commit') != COMMIT
            or manifest.get('source_tree') != TREE or not manifest.get('files')):
        raise ValueError('冻结产物身份不符')
    verified_source = {}
    for relative, expected in manifest['files'].items():
        path = root.joinpath(*safe_relative(relative).split('/'))
        assert_no_links(path)
        if relative.startswith('_internal/app/'):
            content = path.read_bytes()
            actual = hashlib.sha256(content).hexdigest()
            verified_source[relative[len('_internal/app/'):]] = (
                actual, git_hash('blob', content))
        else:
            actual = sha256_file(path)
        if actual != expected:
            raise ValueError('冻结产物发生变化：' + relative)
    for path in (root / '_internal').rglob('*'):
        assert_no_links(path)
        if path.is_file() and path.relative_to(root).as_posix() not in manifest['files']:
            raise ValueError('运行库目录含未列出的文件：' + path.name)
    internal = Path(sys._MEIPASS).resolve()
    if internal != (root / '_internal').resolve():
        raise ValueError('运行库目录不符')
    source = json.loads((internal / 'source-manifest.json').read_text(encoding='utf-8'))
    verify_installed_source(internal / 'app', source, verified_files=verified_source)
    return manifest


CUSTOM_LAYOUT = 'custom-folder-v1'
LEGACY_LAYOUT = 'legacy-default'


def _absolute_directory(value):
    if not isinstance(value, (str, Path)) or not str(value):
        raise ValueError('安装布局必须绑定绝对目录')
    path = Path(value)
    if not path.is_absolute():
        raise ValueError('安装布局必须绑定绝对目录')
    # Check the supplied path before resolving it, so an alias cannot conceal
    # an existing symlink or Windows directory junction.
    assert_no_links(path)
    path = path.resolve()
    assert_no_links(path)
    if path.exists() and not path.is_dir():
        raise ValueError('安装布局路径不是目录：' + str(path))
    return path


def _instance_id(value):
    if (not isinstance(value, str) or len(value) != 32
            or any(character not in '0123456789abcdef' for character in value)):
        raise ValueError('自定义安装实例身份必须为32位小写十六进制文本')
    return value


def _layout_paths(base, layout, instance_id=None):
    if layout == CUSTOM_LAYOUT:
        paths = dict(layout=layout, base=base, root=base / 'app', data=base / 'data',
                     logs_root=base / 'install-logs', rollbacks_root=base / 'rollbacks',
                     backups_root=base / 'backups', instance_id=_instance_id(instance_id))
    else:
        paths = dict(layout=LEGACY_LAYOUT, base=base,
                     root=base / 'HakimiBJTrial' / PACKAGE_ID,
                     data=base / 'HakimiBJTrialData' / PACKAGE_ID,
                     logs_root=base / 'HakimiBJTrialInstallLogs',
                     rollbacks_root=base / 'HakimiBJTrialRollbacks',
                     backups_root=base / 'HakimiBJTrialBackups', instance_id=None)
    for name in ('base', 'root', 'data', 'logs_root', 'rollbacks_root', 'backups_root'):
        paths[name] = _absolute_directory(paths[name])
    if (paths['root'] == paths['data'] or paths['root'] in paths['data'].parents
            or paths['data'] in paths['root'].parents):
        raise ValueError('程序与资料目录不能相交')
    return paths


def installation_layout(root, info, *, expected_commit=COMMIT):
    """Validate stored paths; this does not certify installation completion.

    Existing success and bundle receipts still need their independent checks.
    No directory is adopted merely because it exists or contains files.
    """
    root = _absolute_directory(root)
    if (not isinstance(info, dict) or info.get('package_id') != PACKAGE_ID
            or info.get('commit') != expected_commit):
        raise ValueError('离线试用安装实例身份或路径不符')
    if 'installation_layout' not in info:
        if (root.name != PACKAGE_ID or root.parent.name != 'HakimiBJTrial'
                or 'instance_id' in info or 'container_root' in info):
            raise ValueError('旧默认安装布局身份或路径不符')
        paths = _layout_paths(root.parent.parent, LEGACY_LAYOUT)
    elif info['installation_layout'] == CUSTOM_LAYOUT:
        container = _absolute_directory(info.get('container_root'))
        paths = _layout_paths(container, CUSTOM_LAYOUT, info.get('instance_id'))
    else:
        raise ValueError('未知安装布局；保留原目录')
    if (root != paths['root'] or _absolute_directory(info.get('install_root')) != root
            or _absolute_directory(info.get('data_root')) != paths['data']):
        raise ValueError('离线试用安装实例身份或路径不符')
    return paths


def installation_data(root, info, *, expected_commit=COMMIT):
    """Keep the original Path-returning API for the launcher and old tools."""
    return installation_layout(root, info, expected_commit=expected_commit)['data']


def create_install_layout(destination=None, *, local=None, instance_id=None):
    """Plan a legacy or independent container layout without creating files.

    The installer separately rejects unrecognized nonempty containers and
    authenticates any existing installation before reusing its instance ID.
    """
    if local is None:
        local = os.environ.get('LOCALAPPDATA')
    local = _absolute_directory(local) if local is not None else None
    selected = _absolute_directory(destination) if destination is not None else None
    legacy_root = local / 'HakimiBJTrial' / PACKAGE_ID if local is not None else None
    if selected is None or selected == legacy_root:
        if local is None:
            raise ValueError('默认安装缺少LOCALAPPDATA目录')
        paths = _layout_paths(local, LEGACY_LAYOUT)
        paths['info_fields'] = {}
    else:
        paths = _layout_paths(selected, CUSTOM_LAYOUT,
                              uuid.uuid4().hex if instance_id is None else instance_id)
        paths['info_fields'] = dict(installation_layout=CUSTOM_LAYOUT,
                                   instance_id=paths['instance_id'], container_root=str(paths['base']))
    return paths
