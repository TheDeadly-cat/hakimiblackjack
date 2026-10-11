"""Self-contained per-user setup. No downloads, elevation, or data deletion."""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import traceback
import uuid
import zipfile

from trial_common import assert_no_links, instance_lock, safe_relative, write_json_new
from offline_o3_common import installation_data, installation_layout, create_install_layout
from offline_o3_identity import COMMIT, TREE, PACKAGE_ID, TITLE, EXECUTABLE, UPGRADE_FROM_COMMITS



def show(kind, message):
    import tkinter as tk
    from tkinter import messagebox
    window = tk.Tk()
    window.withdraw()
    try:
        return getattr(messagebox, kind)(TITLE, message, parent=window)
    finally:
        window.destroy()


def shortcut(root, logs, layout=None):
    name = TITLE
    if layout and layout['layout'] == 'custom-folder-v1':
        name += '（' + layout['base'].name + '）-' + layout['instance_id'][:8]
    inputs = logs / 'shortcut-input.json'
    write_json_new(inputs, dict(executable=str(root / EXECUTABLE), root=str(root),
        name=name, receipt=str(logs / 'shortcut-created.json')))
    script = '''$ErrorActionPreference='Stop'
$item=Get-Content -LiteralPath INPUT_FILE -Raw -Encoding UTF8|ConvertFrom-Json
$desktop=[Environment]::GetFolderPath('Desktop')
$linkPath=Join-Path $desktop ($item.name+'.lnk')
$shell=New-Object -ComObject WScript.Shell
if(Test-Path -LiteralPath $linkPath){
    $old=$shell.CreateShortcut($linkPath)
    if($old.TargetPath -ieq $item.executable -and $old.WorkingDirectory -ieq $item.root){
        [IO.File]::Copy($linkPath,(Join-Path (Split-Path -Parent $item.receipt) 'previous-shortcut.lnk'),$false)
    }else{
        $linkPath=Join-Path $desktop ($item.name+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8)+'.lnk')
    }
}
$link=$shell.CreateShortcut($linkPath)
$link.TargetPath=$item.executable
$link.WorkingDirectory=$item.root
$link.Description='Hakimi Blackjack offline trial; realtime acceptance remains incomplete.'
$link.IconLocation=$item.executable
$link.Save()
@{path=$linkPath;target=$item.executable;working_directory=$item.root}|ConvertTo-Json|Set-Content -LiteralPath $item.receipt -Encoding UTF8
'''
    script = script.replace('INPUT_FILE', "'" + str(inputs).replace("'", "''") + "'")
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    process = subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
        '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], capture_output=True,
        text=True, errors='replace', timeout=30, creationflags=0x08000000)
    (logs / 'shortcut.log').write_text(process.stdout + process.stderr, encoding='utf-8')
    if process.returncode:
        raise RuntimeError('桌面快捷方式创建失败；程序与日志保留。')


def existing_install(root, data):
    """Only an existing installation in this exact namespace can be updated."""
    if not root.exists():
        return False
    assert_no_links(root)
    info = json.loads((root / 'install.json').read_text(encoding='utf-8'))
    previous_commit = info.get('commit')
    if previous_commit not in (COMMIT, *UPGRADE_FROM_COMMITS):
        raise ValueError('现有安装源码不在可兼容更新范围；保留原目录')
    if installation_data(root, info, expected_commit=previous_commit) != data.resolve():
        raise ValueError('现有安装的资料目录不符')
    success = json.loads((root / 'INSTALL_SUCCESS.json').read_text(encoding='utf-8'))
    if (success.get('package_id') != PACKAGE_ID or success.get('preflight_passed') is not True
            or success.get('commit') != previous_commit):
        raise ValueError('现有目录不是已完成安装的同版本程序；保留原目录')
    return True


@contextmanager
def program_replacement(root, data, logs, updating, *, layout=None):
    """Retain the old program, keep the data path, and roll back failed updates."""
    layout = layout or create_install_layout(local=root.parent.parent)
    if (root != layout['root'] or data != layout['data'] or logs.parent != layout['logs_root']):
        raise ValueError('更新范围不符')
    planned = dict(package_id=PACKAGE_ID, commit=COMMIT, install_root=str(root), data_root=str(data),
                   **layout.get('info_fields', {}))
    installation_layout(root, planned)
    rollback_base = layout['rollbacks_root']
    assert_no_links(rollback_base)
    token = PACKAGE_ID + '-' + logs.name.rsplit('-', 1)[-1]
    previous = rollback_base / (token + '-previous')
    failed = rollback_base / (token + '-failed')
    if previous.exists() or failed.exists():
        raise ValueError('回滚目录已存在；保留原目录')
    with instance_lock(data):
        if updating:
            existing_install(root, data)
            for path in root.rglob('*'):
                assert_no_links(path)
            rollback_base.mkdir(parents=True, exist_ok=True)
            root.rename(previous)
        elif root.exists():
            raise ValueError('安装目录在预检后出现；保留原目录')
        try:
            yield
        except BaseException:
            if updating:
                if root.exists():
                    root.rename(failed)
                previous.rename(root)
                write_json_new(logs / 'UPDATE_ROLLED_BACK.json',
                               dict(program_restored=str(root), failed_program=str(failed),
                                    data_root=str(data), data_unchanged=True))
            raise
        else:
            if updating:
                write_json_new(logs / 'UPDATE_COMPLETED.json',
                               dict(program_root=str(root), previous_program=str(previous),
                                    data_root=str(data), data_reused=True))


def plan_install(destination=None, *, local=None):
    """Plan one bound instance; an arbitrary nonempty folder is never adopted."""
    layout = create_install_layout(destination, local=local)
    root = layout['root']
    if root.exists():
        info = json.loads((root / 'install.json').read_text(encoding='utf-8'))
        layout = installation_layout(root, info, expected_commit=info.get('commit'))
        layout['info_fields'] = {k: info[k] for k in ('installation_layout','instance_id','container_root') if k in info}
    elif layout['layout'] == 'custom-folder-v1' and layout['base'].exists() and any(layout['base'].iterdir()):
        marker = layout['base'] / 'INSTALL_INSTANCE.json'
        if not marker.is_file():
            raise ValueError('所选文件夹已有其他内容；请选择新文件夹或空文件夹')
        assert_no_links(marker)
        info = json.loads(marker.read_text(encoding='utf-8'))
        if info.get('commit') not in (COMMIT, *UPGRADE_FROM_COMMITS):
            raise ValueError('所选文件夹的既有安装身份不符')
        layout = installation_layout(root, info, expected_commit=info['commit'])
        layout['info_fields'] = {k: info[k] for k in ('installation_layout','instance_id','container_root')}
    return layout


def install(reuse_data=False, destination=None, progress=None):
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        raise ValueError('需要冻结的 Windows 安装入口')
    notify = progress or (lambda message: None)
    notify('检查安装位置')
    layout = plan_install(destination)
    root, data = layout['root'], layout['data']
    assert_no_links(root)
    assert_no_links(data)
    updating = existing_install(root, data)
    if data.exists() and (not data.is_dir() or any(data.iterdir())) and not reuse_data and not updating:
        raise ValueError('试用资料目录非空，默认拒绝复用；保留原资料。')
    internal = Path(sys._MEIPASS)
    notify('核验安装包')
    payload = internal / 'payload/runtime.zip'
    info = json.loads((internal / 'payload/PAYLOAD.json').read_text(encoding='utf-8'))
    if (info.get('package_id') != PACKAGE_ID or info.get('commit') != COMMIT
            or info.get('source_tree') != TREE
            or hashlib.sha256(payload.read_bytes()).hexdigest() != info.get('zip_sha256')):
        raise ValueError('内置产物身份或摘要不符')
    logs = layout['logs_root'] / (PACKAGE_ID + '-' + datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8])
    assert_no_links(logs)
    logs.mkdir(parents=True, exist_ok=False)
    with program_replacement(root, data, logs, updating, layout=layout):
        result = install_payload(root, data, payload, info, logs, layout=layout, progress=notify)
        if layout['layout'] == 'custom-folder-v1':
            marker = layout['base'] / 'INSTALL_INSTANCE.json'
            binding = json.loads((root / 'install.json').read_text(encoding='utf-8'))
            if marker.exists():
                # Existing instance identity stays fixed across compatible updates.
                previous = json.loads(marker.read_text(encoding='utf-8'))
                installation_layout(root, previous, expected_commit=previous['commit'])
                if previous['instance_id'] != layout['instance_id']:
                    raise ValueError('已有资料的安装实例身份改变')
            else:
                write_json_new(marker, binding)
        return result


def install_payload(root, data, payload, info, logs, *, layout=None, progress=None):
    notify = progress or (lambda message: None)
    try:
        notify('解压程序和运行库')
        with zipfile.ZipFile(payload) as archive:
            members = archive.infolist()
            names = set()
            expanded = 0
            for member in members:
                name = safe_relative(member.filename)
                if member.is_dir() or name.casefold() in names:
                    raise ValueError('内置压缩包包含重复或异常项')
                names.add(name.casefold())
                kind = stat.S_IFMT(member.external_attr >> 16)
                if kind not in (0, stat.S_IFREG):
                    raise ValueError('内置压缩包含链接或特殊文件')
                if member.flag_bits & 1:
                    raise ValueError('不接受加密产物')
                expanded += member.file_size
                if expanded > 512 * 1024 * 1024 or member.file_size > 128 * 1024 * 1024:
                    raise ValueError('内置产物大小超过限制')
            if len(names) > 10000 or len(names) != len(info['files']):
                raise ValueError('内置产物数量不符')
            root.mkdir(parents=True, exist_ok=False)
            for member in members:
                name = safe_relative(member.filename)
                content = archive.read(member)
                if hashlib.sha256(content).hexdigest() != info['files'].get(name):
                    raise ValueError('内置产物文件摘要不符：' + name)
                target = root.joinpath(*name.split('/'))
                assert_no_links(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as stream:
                    stream.write(content)
        data.mkdir(parents=True, exist_ok=True)
        write_json_new(root / 'install.json', dict(package_id=PACKAGE_ID, commit=COMMIT,
            source_tree=TREE, install_root=str(root), data_root=str(data),
            installer_kind='offline-frozen-onedir', signed=False, **(layout or {}).get('info_fields', {})))
        receipt = logs / 'frozen-preflight.json'
        notify('检查运行环境与程序')
        run = subprocess.run([str(root / EXECUTABLE), '--preflight', '--receipt', str(receipt)],
            timeout=90, creationflags=0x08000000)
        result = json.loads(receipt.read_text(encoding='utf-8')) if receipt.is_file() else {}
        if run.returncode or result.get('preflight_passed') is not True or result.get('package_id') != PACKAGE_ID:
            raise RuntimeError('冻结程序目标机预检未通过；不能生成成功标记。')
        notify('创建桌面快捷方式')
        shortcut(root, logs, layout)
        success = dict(package_id=PACKAGE_ID, commit=COMMIT, preflight_passed=True,
            installed_at=datetime.now().astimezone().isoformat(), logs=str(logs),
            native_UI_acceptance=False, ordinary_permissions_acceptance=False,
            clean_no_python_host_test=False, disconnected_host_test=False,
            realtime_accepted=False, signed=False)
        write_json_new(root / 'INSTALL_SUCCESS.json', success)
        write_json_new(logs / 'INSTALL_SUCCESS.json', success)
        return root, logs
    except Exception:
        write_json_new(logs / 'INSTALL_FAILED.json', dict(package_id=PACKAGE_ID,
            error=traceback.format_exc(), original_data_preserved=True))
        raise


def main():
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--reuse-data', action='store_true')
    parser.add_argument('--install-dir', type=Path, help='自定义安装文件夹；各文件夹使用独立资料')
    args = parser.parse_args()
    try:
        if not args.yes:
            from offline_o3_setup_ui import run_installer
            return run_installer(install, initial=args.install_dir, reuse_data=args.reuse_data)
        install(args.reuse_data, args.install_dir)
        return 0
    except Exception:
        if not args.yes:
            show('showerror', traceback.format_exc()[-1600:])
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
