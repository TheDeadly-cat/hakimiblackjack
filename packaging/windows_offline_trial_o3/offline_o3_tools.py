"""Frozen maintenance; whole-directory backups and an external uninstall runner."""
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import traceback
import uuid

from offline_o3_common import COMMIT, PACKAGE_ID, TITLE, installation_layout, program_root, verify_bundle
from offline_o3_identity import EXECUTABLE
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
    path = root / 'install.json'
    assert_no_links(path)
    info = json.loads(path.read_text(encoding='utf-8'))
    return info, installation_layout(root, info)


def result_path(logs, action):
    assert_no_links(logs)
    logs.mkdir(parents=True, exist_ok=True)
    return logs / (PACKAGE_ID + '-' + action + '-' + datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8] + '.json')


def _absolute(value):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError('卸载引用必须绑定绝对路径')
    path = Path(value)
    assert_no_links(path)
    return path.resolve()


def _uninstall_layout(runner, reference):
    """Re-read the target's identity; a runner cannot retarget another instance."""
    target = _absolute(reference['target'])
    base = _absolute(reference['installation_base'])
    info, layout = _install(target)
    if reference.get('layout') is None:
        # Already generated legacy dispatchers had these three fields only.
        if layout['layout'] != 'legacy-default':
            raise ValueError('旧卸载引用不能定位自定义安装实例')
        expected = base / 'HakimiBJTrial' / PACKAGE_ID
    else:
        expected = layout['root']
        if (reference.get('layout') != layout['layout']
                or _absolute(reference['container_root']) != layout['base']
                or _absolute(reference['data_root']) != layout['data']
                or reference.get('instance_id') != layout['instance_id']
                or reference.get('install_info_sha256') != sha256_file(target / 'install.json')):
            raise ValueError('卸载引用与原安装实例的资料、容器或身份不符')
        if ('prepare_pid' not in reference or 'prepare_creation_ticks' not in reference):
            raise ValueError('新卸载引用缺少原准备进程身份')
    if (reference.get('package_id') != PACKAGE_ID or target != expected
            or target != layout['root'] or base != layout['base']
            or runner == target or runner.is_relative_to(target)
            or runner.parent != layout['logs_root']
            or not runner.name.startswith('offline-uninstall-runner-')
            or layout['data'].is_relative_to(target) or target.is_relative_to(layout['data'])):
        raise ValueError('卸载外部运行目录或目标不符')
    return layout


def _process_creation(kernel, handle):
    import ctypes
    from ctypes import wintypes
    values = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
        raise ctypes.WinError(ctypes.get_last_error())
    return values[0].dwLowDateTime | (values[0].dwHighDateTime << 32)


def _process_api():
    import ctypes
    from ctypes import wintypes
    if os.name != 'nt':
        raise RuntimeError('离线卸载派发仅支持Windows')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def _prepare_origin():
    kernel = _process_api()
    return dict(prepare_pid=os.getpid(),
                prepare_creation_ticks=_process_creation(kernel, kernel.GetCurrentProcess()))


def _wait_for_prepare_exit(reference):
    """Wait for the exact preparing process, never a reused PID or a live app."""
    if 'prepare_pid' not in reference and 'prepare_creation_ticks' not in reference:
        return  # The legacy CMD already waited for preparation to finish.
    pid, creation = reference.get('prepare_pid'), reference.get('prepare_creation_ticks')
    if type(pid) is not int or not 0 < pid <= 0xffffffff or type(creation) is not int or creation <= 0 or pid == os.getpid():
        raise ValueError('卸载派发的原进程身份不符')
    import ctypes
    kernel = _process_api()
    handle = kernel.OpenProcess(0x100000 | 0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:  # The original PID has already exited.
            return
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if _process_creation(kernel, handle) != creation:
            return  # A different creation identity proves the original is gone.
        status = kernel.WaitForSingleObject(handle, 15000)
        if status != 0:
            raise RuntimeError('原卸载准备程序尚未确认退出；本次保留程序，不重试删除')
    finally:
        kernel.CloseHandle(handle)


def _dispatch_uninstall(runner, logs, *, yes):
    command = [str(runner / EXECUTABLE), '--maintenance', 'uninstall',
               '--receipt', str(runner / 'UNINSTALL_RESULT.json')]
    if yes:
        command.append('--yes')
    return subprocess.Popen(command, cwd=logs,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def maintain(action, yes=False, receipt_path=None):
    result = dict(package_id=PACKAGE_ID, commit=COMMIT, action=action, completed=False)
    try:
        root = program_root()
        verify_bundle(root)
        if action == 'uninstall':
            reference_path = root / 'UNINSTALL_TARGET.json'
            assert_no_links(reference_path)
            reference = json.loads(reference_path.read_text(encoding='utf-8'))
            layout = _uninstall_layout(root, reference)
            target, data = layout['root'], layout['data']
            if receipt_path is None:
                receipt_path = result_path(layout['logs_root'], action)
            receipt = Path(receipt_path).resolve()
            if receipt.is_relative_to(target) or receipt.is_relative_to(data):
                receipt_path = result_path(layout['logs_root'], action)
                raise ValueError('卸载结果必须保存在程序及资料目录之外')
            _wait_for_prepare_exit(reference)
            if not yes and not confirm(f'卸载本离线试用程序？\n{target}\n\n全部资料保留：{data}'):
                return 0
            # Revalidate the resolved target and every child before recursive
            # deletion. No shell-built filesystem command participates.
            with instance_lock(data):
                if _uninstall_layout(root, reference) != layout:
                    raise ValueError('卸载确认期间安装实例发生变化；保留程序')
                assert_no_links(target)
                for path in target.rglob('*'):
                    assert_no_links(path)
                    if not path.resolve().is_relative_to(target):
                        raise ValueError('卸载目录越界')
                before = snapshot_files(data)
                shutil.rmtree(target)
                if snapshot_files(data) != before:
                    raise RuntimeError('卸载后的资料核对不符')
            result.update(program_removed=True, data_preserved=True, files=before)
        else:
            info, layout = _install(root)
            data = layout['data']
            if receipt_path is None:
                receipt_path = result_path(layout['logs_root'], action)
            if action == 'verify':
                result['complete_bundle_and_source_verified'] = True
            elif action == 'backup':
                if not yes and not confirm('请正常关闭试用窗口，等待保存结束。\n\n复制完整试用资料？'):
                    return 0
                destination = layout['backups_root'] / (PACKAGE_ID + '-' + datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8])
                result.update(backup(data, destination), backup_directory=str(destination))
            elif action == 'prepare-uninstall':
                logs = layout['logs_root']
                assert_no_links(logs)
                logs.mkdir(parents=True, exist_ok=True)
                runner = logs / ('offline-uninstall-runner-' + uuid.uuid4().hex)
                with instance_lock(data):
                    shutil.copytree(root, runner)
                write_json_new(runner / 'UNINSTALL_TARGET.json', dict(package_id=PACKAGE_ID,
                    target=str(root), installation_base=str(layout['base']),
                    layout=layout['layout'], container_root=str(layout['base']),
                    data_root=str(data), instance_id=layout['instance_id'],
                    install_info_sha256=sha256_file(root / 'install.json'), **_prepare_origin()))
                command = logs / (PACKAGE_ID + '-uninstall.cmd')
                if command.exists():
                    # Preserve the previous dispatcher before publishing a new
                    # one for this same verified installation namespace.
                    preserved = logs / (PACKAGE_ID + '-uninstall-' + uuid.uuid4().hex + '.cmd')
                    shutil.copy2(command, preserved)
                body = '@echo off\r\nchcp 65001 >nul\r\ncd /d "%~dp0"\r\nif errorlevel 1 exit /b 1\r\nstart /wait "" "' + str(runner / EXECUTABLE) + '" --maintenance uninstall --receipt "' + str(runner / 'UNINSTALL_RESULT.json') + '" %*\r\nexit /b %errorlevel%\r\n'
                command.write_bytes(body.encode('utf-8'))
                dispatched = _dispatch_uninstall(runner, logs, yes=yes)
                result.update(external_runner=str(runner), dispatcher=str(command),
                    external_runner_dispatched=True, external_runner_pid=dispatched.pid,
                    actual_uninstall=False)
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
