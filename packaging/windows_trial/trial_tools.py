"""Maintenance for this TRIAL instance only. Data is never deleted by uninstall."""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import uuid

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from trial_common import (COMMIT, PACKAGE_ID, assert_no_links, instance_lock, load_install,
                          sha256_file, verify_installed_source, write_json_new)


def snapshot_files(root: Path) -> dict[str, str]:
    output = {}
    for path in sorted(root.rglob('*')):
        assert_no_links(path)
        if path.is_file() and path.name != '.trial-instance.lock':
            output[path.relative_to(root).as_posix()] = sha256_file(path)
    return output


def backup(data: Path, destination: Path) -> dict:
    assert_no_links(data); assert_no_links(destination)
    if destination.exists() or destination.resolve().is_relative_to(data.resolve()):
        raise ValueError('备份目标必须是试用数据目录之外的新目录')
    with instance_lock(data):
        before = snapshot_files(data)
        database = data / 'session.db'
        if database.exists():
            with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
                result = connection.execute('PRAGMA quick_check').fetchall()
                if result != [('ok',)]:
                    raise RuntimeError('SQLite 快检未通过，请保留原资料并人工核对')
        # Never write into an existing destination or back up only the .db.
        destination.mkdir(parents=True, exist_ok=False)
        target = destination / 'data'
        target.mkdir()
        for relative in before:
            source_file = data.joinpath(*relative.split('/'))
            target_file = target.joinpath(*relative.split('/'))
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target_file)
        copied = snapshot_files(target)
        after = snapshot_files(data)
        if before != copied or before != after:
            write_json_new(destination / 'BACKUP_INCOMPLETE.json', {'reason': '源或副本在复制时发生变化'})
            raise RuntimeError('资料在复制时发生变化；副本已标为不完整，不可当作成功备份')
        receipt = dict(package_id=PACKAGE_ID, commit=COMMIT, files=before,
            created_at=datetime.now().astimezone().isoformat(), data_copied_and_hash_verified=True,
            application_restore_tested=False,
            note='复制校验不是应用恢复验收；不自动改写快照内部路径或导入旧版数据。')
        write_json_new(destination / 'BACKUP_MANIFEST.json', receipt)
        return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description='试用版资料与源码维护；卸载不删除数据')
    parser.add_argument('action', choices=('backup', 'open-data', 'verify', 'uninstall'))
    args = parser.parse_args()
    try:
        info = load_install(ROOT, require_success=args.action != 'uninstall')
        data = Path(info['data_root'])
        assert_no_links(data)
        if args.action == 'verify':
            manifest = json.loads((ROOT / 'source-manifest.json').read_text(encoding='utf-8'))
            verify_installed_source(ROOT / 'app', manifest)
            print('固定源码逐文件及完整 Git 树校验通过。未验证实时性能。')
        elif args.action == 'open-data':
            data.mkdir(parents=True, exist_ok=True)
            os.startfile(data)
        elif args.action == 'backup':
            print('请确认试用窗口和相关工作进程已经正常退出。不要从另一个入口打开相同数据库。')
            if input('输入 BACKUP 复制完整试用资料：').strip() != 'BACKUP':
                print('已取消。'); return 0
            folder = Path(os.environ['LOCALAPPDATA']) / 'HakimiBJTrialBackups'
            target = folder / (PACKAGE_ID + '-' + datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8])
            receipt = backup(data, target)
            print(f'已复制并核对 {len(receipt["files"])} 个文件：{target}')
            print('尚未执行独立目录的应用恢复验收；原资料未修改。')
        else:
            expected = (Path(os.environ['LOCALAPPDATA']) / 'HakimiBJTrial' / PACKAGE_ID).resolve()
            if ROOT.resolve() != expected or data.resolve().is_relative_to(ROOT.resolve()):
                raise ValueError('卸载目录不符合本试用版固定边界，已拒绝删除')
            print('只移除本试用版程序目录：' + str(ROOT))
            print('保留全部试用数据：' + str(data))
            print('不卸载基础 Python，不修改旧版。桌面试用快捷方式请手动删除。')
            if input('输入 UNINSTALL 确认卸载程序：').strip() != 'UNINSTALL':
                print('已取消。'); return 0
            if Path(sys.executable).resolve().is_relative_to(ROOT.resolve()):
                raise RuntimeError('请通过 UNINSTALL_TRIAL.cmd 使用外部基础 Python 卸载，避免删除运行中的解释器')
            with instance_lock(data):
                os.chdir(Path(os.environ.get('TEMP', os.environ['LOCALAPPDATA'])))
                assert_no_links(ROOT)
                # Refuse nested reparse points rather than traverse their targets.
                for item in ROOT.rglob('*'):
                    assert_no_links(item)
                shutil.rmtree(ROOT)
            print('试用版程序已卸载，数据完整保留。')
        return 0
    except Exception as error:
        print('未完成：' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
