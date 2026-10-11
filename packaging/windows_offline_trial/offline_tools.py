"""Frozen maintenance; whole-directory backups and an external uninstall runner."""
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import shutil
import sqlite3
import traceback
import uuid

from offline_common import COMMIT, PACKAGE_ID, TITLE, installation_data, program_root, verify_bundle
from trial_common import assert_no_links, instance_lock, sha256_file, write_json_new


def snapshot_files(root):
    result = {}
    for path in sorted(root.rglob('*')):
        assert_no_links(path)
        if path.is_file() and path.name != '.trial-instance.lock':
            result[path.relative_to(root).as_posix()] = sha256_file(path)
    return result


def backup(data, destination):
    assert_no_links(data)
    assert_no_links(destination)
    if destination.exists() or destination.resolve().is_relative_to(data.resolve()):
        raise ValueError('备份必须使用资料目录之外的新目录')
    with instance_lock(data):
        before = snapshot_files(data)
        database = data / 'session.db'
        if database.exists():
            with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
                if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                    raise ValueError('数据库快检失败；保留资料')
        destination.mkdir(parents=True, exist_ok=False)
        copied_root = destination / 'data'
        copied_root.mkdir()
        for relative in before:
            source = data.joinpath(*relative.split('/'))
            target = copied_root.joinpath(*relative.split('/'))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        if snapshot_files(data) != before or snapshot_files(copied_root) != before:
            write_json_new(destination / 'BACKUP_INCOMPLETE.json', dict(reason='复制时内容改变'))
            raise ValueError('资料或副本改变；副本未通过验收')
        result = dict(package_id=PACKAGE_ID, commit=COMMIT, files=before,
            data_copied_and_hash_verified=True, application_restore_tested=False,
            created_at=datetime.now().astimezone().isoformat())
        write_json_new(destination / 'BACKUP_MANIFEST.json', result)
        return result


def confirm(message):
    import tkinter as tk
    from tkinter import messagebox
    window = tk.Tk()
    window.withdraw()
    try:
        return messagebox.askyesno(TITLE, message, parent=window)
    finally:
        window.destroy()


def _install(root):
    info = json.loads((root / 'install.json').read_text(encoding='utf-8'))
    return info, installation_data(root, info)


def result_path(base, action):
    logs = base / 'HakimiBJTrialInstallLogs'
    assert_no_links(logs)
    logs.mkdir(parents=True, exist_ok=True)
    return logs / (PACKAGE_ID + '-' + action + '-' + datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8] + '.json')


def maintain(action, yes=False, receipt_path=None):
    result = dict(package_id=PACKAGE_ID, commit=COMMIT, action=action, completed=False)
    try:
        root = program_root()
        verify_bundle(root)
        if action == 'uninstall':
            reference = json.loads((root / 'UNINSTALL_TARGET.json').read_text(encoding='utf-8'))
            target = Path(reference['target']).resolve()
            base = Path(reference['installation_base']).resolve()
            expected = base / 'HakimiBJTrial' / PACKAGE_ID
            if (reference.get('package_id') != PACKAGE_ID or target != expected
                    or root == target or root.is_relative_to(target)
                    or root.parent.name != 'HakimiBJTrialInstallLogs'
                    or not root.name.startswith('offline-uninstall-runner-')
                    or base != root.parent.parent):
                raise ValueError('卸载外部运行目录或目标不符')
            info, data = _install(target)
            if target != Path(info['install_root']).resolve() or data.is_relative_to(target):
                raise ValueError('卸载不能处理数据目录')
            if receipt_path is None:
                receipt_path = result_path(base, action)
            if not yes and not confirm(f'卸载本离线试用程序？\n{target}\n\n全部资料保留：{data}'):
                return 0
            # Revalidate the resolved target and every child before recursive
            # deletion. No shell-built filesystem command participates.
            assert_no_links(target)
            for path in target.rglob('*'):
                assert_no_links(path)
                if not path.resolve().is_relative_to(expected):
                    raise ValueError('卸载目录越界')
            with instance_lock(data):
                before = snapshot_files(data)
                shutil.rmtree(target)
                if snapshot_files(data) != before:
                    raise RuntimeError('卸载后的资料核对不符')
            result.update(program_removed=True, data_preserved=True, files=before)
        else:
            info, data = _install(root)
            base = root.parent.parent
            if receipt_path is None:
                receipt_path = result_path(base, action)
            if action == 'verify':
                result['complete_bundle_and_source_verified'] = True
            elif action == 'backup':
                if not yes and not confirm('请正常关闭试用窗口，等待保存结束。\n\n复制完整试用资料？'):
                    return 0
                destination = base / 'HakimiBJTrialBackups' / (PACKAGE_ID + '-' + datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8])
                result.update(backup(data, destination), backup_directory=str(destination))
            elif action == 'prepare-uninstall':
                logs = base / 'HakimiBJTrialInstallLogs'
                assert_no_links(logs)
                logs.mkdir(parents=True, exist_ok=True)
                runner = logs / ('offline-uninstall-runner-' + uuid.uuid4().hex)
                with instance_lock(data):
                    shutil.copytree(root, runner)
                write_json_new(runner / 'UNINSTALL_TARGET.json', dict(package_id=PACKAGE_ID,
                    target=str(root), installation_base=str(base)))
                command = logs / (PACKAGE_ID + '-uninstall.cmd')
                if command.exists():
                    # Preserve the previous dispatcher before publishing a new
                    # one for this same verified installation namespace.
                    preserved = logs / (PACKAGE_ID + '-uninstall-' + uuid.uuid4().hex + '.cmd')
                    shutil.copy2(command, preserved)
                body = '@echo off\r\nchcp 65001 >nul\r\ncd /d "%~dp0"\r\nif errorlevel 1 exit /b 1\r\nstart /wait "" "' + str(runner / 'HakimiBlackjackTrialT1O1.exe') + '" --maintenance uninstall --receipt "' + str(runner / 'UNINSTALL_RESULT.json') + '" %*\r\nexit /b %errorlevel%\r\n'
                command.write_bytes(body.encode('utf-8'))
                result.update(external_runner=str(runner), dispatcher=str(command), actual_uninstall=False)
            else:
                raise ValueError('未知维护动作')
        result['completed'] = True
        code = 0
    except Exception:
        result['error'] = traceback.format_exc()
        code = 1
    if receipt_path:
        write_json_new(Path(receipt_path), result)
    if not yes and (code or action != 'prepare-uninstall'):
        import tkinter as tk
        from tkinter import messagebox
        window = tk.Tk()
        window.withdraw()
        if code:
            messagebox.showerror(TITLE, result['error'][-1600:], parent=window)
        else:
            detail = {'verify': '完整运行库和固定源码核验通过。',
                'backup': '完整资料复制与摘要核对通过。\n应用恢复仍须单独验收。\n' + result.get('backup_directory', ''),
                'uninstall': '本试用程序已卸载，全部资料保留。'}.get(action, '维护完成。')
            messagebox.showinfo(TITLE, detail + '\n\n结果记录：' + str(receipt_path), parent=window)
        window.destroy()
    return code
