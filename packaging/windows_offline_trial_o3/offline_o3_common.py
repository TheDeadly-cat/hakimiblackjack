"""Identity and read-only verification for the isolated frozen trial."""
from pathlib import Path
import hashlib
import json
import sys

from trial_common import assert_no_links, git_hash, sha256_file, safe_relative, tree_hash
from offline_o3_identity import COMMIT, TREE, SOURCE_FILE_COUNT, PACKAGE_ID, TITLE

def verify_installed_source(root, manifest):
    """Verify this complete fixed Git tree without changing online R4's globals."""
    if (manifest.get('commit') != COMMIT or manifest.get('git_tree') != TREE
            or len(manifest.get('files', {})) != SOURCE_FILE_COUNT):
        raise ValueError('安装清单与固定离线试用版不一致')
    entries = {}
    for relative, item in manifest['files'].items():
        path = root.joinpath(*safe_relative(relative).split('/'))
        assert_no_links(path)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('源码发生变化，已停止启动：' + relative)
        blob = git_hash('blob', data)
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
    for relative, expected in manifest['files'].items():
        path = root.joinpath(*safe_relative(relative).split('/'))
        assert_no_links(path)
        if sha256_file(path) != expected:
            raise ValueError('冻结产物发生变化：' + relative)
    for path in (root / '_internal').rglob('*'):
        assert_no_links(path)
        if path.is_file() and path.relative_to(root).as_posix() not in manifest['files']:
            raise ValueError('运行库目录含未列出的文件：' + path.name)
    internal = Path(sys._MEIPASS).resolve()
    if internal != (root / '_internal').resolve():
        raise ValueError('运行库目录不符')
    source = json.loads((internal / 'source-manifest.json').read_text(encoding='utf-8'))
    verify_installed_source(internal / 'app', source)
    return manifest


def installation_data(root, info):
    """Use the stored installation namespace, including a Chinese test base.

    The desktop shell need not inherit an installer's process-local environment.
    Moving the installed program still fails its absolute-root binding.
    """
    root = Path(root).resolve()
    expected_data = root.parent.parent / 'HakimiBJTrialData' / PACKAGE_ID
    if (root.name != PACKAGE_ID or root.parent.name != 'HakimiBJTrial'
            or info.get('package_id') != PACKAGE_ID or info.get('commit') != COMMIT
            or Path(info['install_root']).resolve() != root
            or Path(info['data_root']).resolve() != expected_data):
        raise ValueError('离线试用安装实例身份或路径不符')
    assert_no_links(root)
    assert_no_links(expected_data)
    return expected_data
