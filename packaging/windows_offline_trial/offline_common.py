"""Identity and read-only verification for the isolated frozen trial."""
from pathlib import Path
import json
import sys

from trial_common import assert_no_links, sha256_file, safe_relative, verify_installed_source

COMMIT = '3ec2006e82c4bdbc8da01bb0e1212b64c9111d2c'
TREE = '7fc1e8bb64c5bc4a783941fee536f394acf1fff7'
PACKAGE_ID = 'T1O1-3ec2006'
TITLE = 'Hakimi Blackjack 离线试用版 T1O1'


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
