"""Self-contained per-user setup. No downloads, elevation, or data deletion."""
import argparse
import base64
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

from trial_common import assert_no_links, safe_relative, write_json_new

PACKAGE_ID = 'T1O1-3ec2006'
COMMIT = '3ec2006e82c4bdbc8da01bb0e1212b64c9111d2c'
TREE = '7fc1e8bb64c5bc4a783941fee536f394acf1fff7'
TITLE = 'Hakimi Blackjack 离线试用版 T1O1'
EXECUTABLE = 'HakimiBlackjackTrialT1O1.exe'


def show(kind, message):
    import tkinter as tk
    from tkinter import messagebox
    window = tk.Tk()
    window.withdraw()
    try:
        return getattr(messagebox, kind)(TITLE, message, parent=window)
    finally:
        window.destroy()


def shortcut(root, logs):
    inputs = logs / 'shortcut-input.json'
    write_json_new(inputs, dict(executable=str(root / EXECUTABLE), root=str(root),
        name=TITLE, receipt=str(logs / 'shortcut-created.json')))
    script = '''$ErrorActionPreference='Stop'
$item=Get-Content -LiteralPath INPUT_FILE -Raw -Encoding UTF8|ConvertFrom-Json
$desktop=[Environment]::GetFolderPath('Desktop')
$linkPath=Join-Path $desktop ($item.name+'.lnk')
if(Test-Path -LiteralPath $linkPath){$linkPath=Join-Path $desktop ($item.name+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8)+'.lnk')}
$shell=New-Object -ComObject WScript.Shell
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


def install(reuse_data=False):
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        raise ValueError('需要冻结的 Windows 安装入口')
    local = Path(os.environ['LOCALAPPDATA']).resolve()
    root = local / 'HakimiBJTrial' / PACKAGE_ID
    data = local / 'HakimiBJTrialData' / PACKAGE_ID
    assert_no_links(root)
    assert_no_links(data)
    if root.exists():
        raise ValueError('程序目录已存在，拒绝覆盖。保留旧产物与资料。')
    if data.exists() and (not data.is_dir() or any(data.iterdir())) and not reuse_data:
        raise ValueError('试用资料目录非空，默认拒绝复用；保留原资料。')
    internal = Path(sys._MEIPASS)
    payload = internal / 'payload/runtime.zip'
    info = json.loads((internal / 'payload/PAYLOAD.json').read_text(encoding='utf-8'))
    if (info.get('package_id') != PACKAGE_ID or info.get('commit') != COMMIT
            or info.get('source_tree') != TREE
            or hashlib.sha256(payload.read_bytes()).hexdigest() != info.get('zip_sha256')):
        raise ValueError('内置产物身份或摘要不符')
    logs = local / 'HakimiBJTrialInstallLogs' / (PACKAGE_ID + '-' + datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8])
    assert_no_links(logs)
    logs.mkdir(parents=True, exist_ok=False)
    try:
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
            installer_kind='offline-frozen-onedir', signed=False))
        receipt = logs / 'frozen-preflight.json'
        run = subprocess.run([str(root / EXECUTABLE), '--preflight', '--receipt', str(receipt)],
            timeout=90, creationflags=0x08000000)
        result = json.loads(receipt.read_text(encoding='utf-8')) if receipt.is_file() else {}
        if run.returncode or result.get('preflight_passed') is not True or result.get('package_id') != PACKAGE_ID:
            raise RuntimeError('冻结程序目标机预检未通过；不能生成成功标记。')
        shortcut(root, logs)
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
    args = parser.parse_args()
    try:
        if not args.yes and not show('askyesno',
                '安装独立的离线试用版 T1O1？\n\n包含 Python/Tk；无需联网下载源码。\n'
                '目标 Windows 仍须具备 .NET Framework 4 运行时，不自动安装系统组件。\n'
                '程序未签名，实时性能验收未通过；不覆盖旧版，不导入日常资料。'):
            return 0
        root, logs = install(args.reuse_data)
        if not args.yes:
            show('showinfo', f'安装预检完成：{root}\n\n日志：{logs}\n'
                '这不是完整应用、普通权限或实时性能验收通过。')
        return 0
    except Exception:
        if not args.yes:
            show('showerror', traceback.format_exc()[-1600:])
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
