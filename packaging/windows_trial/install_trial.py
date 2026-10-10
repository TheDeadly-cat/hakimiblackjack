"""Per-user online/source TRIAL installation; does not modify the user's repository.

Requires an existing Windows x64 Python 3.14 with Tk and .NET Framework's
compiler. This kit intentionally does not pretend to be an offline EXE.
"""
from __future__ import annotations
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import traceback
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import uuid
import venv

from trial_common import (ARCHIVE_URL, COMMIT, TREE, PACKAGE_ID, TITLE, MAX_ARCHIVE_BYTES,
    assert_no_links, extract_verified, inspect_archive, sha256_file, write_json_new)

HERE = Path(__file__).resolve().parent


def download_source(destination: Path) -> None:
    request = Request(ARCHIVE_URL, headers={'User-Agent': 'Hakimi-Blackjack-Trial-Installer/1'})
    start = time.monotonic()
    with urlopen(request, timeout=30) as response, destination.open('xb') as output:
        if urlsplit(response.geturl()).scheme != 'https' or urlsplit(response.geturl()).hostname not in (
                'github.com', 'codeload.github.com'):
            raise ValueError('源码下载重定向到非允许域名')
        size = 0
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_ARCHIVE_BYTES or time.monotonic() - start > 240:
                raise RuntimeError('源码下载超过大小或时间上限，已停止')
            output.write(chunk)


def prerequisites() -> dict:
    if os.name != 'nt' or struct.calcsize('P') != 8 or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('本安装包仅面向 Windows x64；不在此处测试 ARM64 或其他系统。')
    if sys.version_info[:2] != (3, 14):
        raise RuntimeError('请使用现有的 Windows x64 Python 3.14（含 Tcl/Tk）运行。安装器不会自动安装系统组件。')
    import tkinter as tk
    import sqlite3
    import ssl
    root = tk.Tk(); root.withdraw(); root.update_idletasks()
    tk_version = str(root.tk.call('info', 'patchlevel')); root.destroy()
    compiler = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if not compiler.is_file():
        raise RuntimeError(f'缺少 .NET Framework 编译器：{compiler}。\n请先由设备使用者处理；安装器不会提权或自动修改 Windows 组件。')
    return {'python': sys.version, 'base_python': str(Path(getattr(sys, '_base_executable', sys.executable)).resolve()),
            'tk': tk_version, 'sqlite': sqlite3.sqlite_version, 'ssl': ssl.OPENSSL_VERSION,
            'compiler': str(compiler), 'platform': platform.platform()}


def cmd_quote(value: str) -> str:
    if any(c in value for c in ('"', '\n', '\r')):
        raise ValueError('命令路径含无效字符')
    return '"' + value.replace('%', '%%') + '"'


def batch_file(path: Path, lines: list[str]) -> None:
    path.write_bytes(('\r\n'.join(['@echo off', 'setlocal DisableDelayedExpansion', 'chcp 65001 >nul', *lines]) + '\r\n').encode('utf-8'))


def uninstall_batch(path: Path, runner: Path, base_python: str) -> None:
    # Transfer batch control (no CALL) before removing the program directory.
    # The external runner remains readable and reports the Python result exactly.
    if runner.resolve().is_relative_to(path.parent.resolve()):
        raise ValueError('Uninstall runner must be outside the program directory')
    if path.exists() or runner.exists():
        raise FileExistsError('Refusing to overwrite an uninstall entry')
    batch_file(runner, [
        'cd /d "%TEMP%"', cmd_quote(base_python) + ' -B ' +
        cmd_quote(str(path.parent / 'trial_tools.py')) + ' uninstall',
        'set "CODE=%ERRORLEVEL%"', 'pause', 'exit /b %CODE%'])
    batch_file(path, ['cd /d "%TEMP%"', cmd_quote(str(runner))])


def create_shortcut(root: Path) -> str | None:
    """Use a user-owned unique shortcut name; never overwrite an existing link."""
    env = os.environ.copy()
    env['HAKIMI_TRIAL_TARGET'] = str(root / 'runtime/Scripts/pythonw.exe')
    env['HAKIMI_TRIAL_SCRIPT'] = str(root / 'launch_trial.py')
    env['HAKIMI_TRIAL_ROOT'] = str(root)
    env['HAKIMI_TRIAL_TITLE'] = TITLE
    command = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$desktop = [Environment]::GetFolderPath('Desktop')
if (-not $desktop) { throw 'Desktop unavailable' }
$name = $env:HAKIMI_TRIAL_TITLE
$link = Join-Path $desktop ($name + '.lnk')
if (Test-Path -LiteralPath $link) {
  $link = Join-Path $desktop ($name + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8) + '.lnk')
}
$shell = New-Object -ComObject WScript.Shell
$item = $shell.CreateShortcut($link)
$item.TargetPath = $env:HAKIMI_TRIAL_TARGET
$item.Arguments = '-I -B "' + $env:HAKIMI_TRIAL_SCRIPT + '"'
$item.WorkingDirectory = $env:HAKIMI_TRIAL_ROOT
$item.Description = 'TRIAL / 试用版；独立数据；实时性能尚未验收'
$item.Save()
Write-Output $link
'''
    # No execution-policy change is necessary for this inline, explicit command.
    import base64
    encoded = base64.b64encode(command.encode('utf-16le')).decode('ascii')
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, env=env, timeout=25)
    if result.returncode:
        print('未能创建桌面快捷方式；安装目录中的 START_TRIAL.cmd 仍可使用。')
        return None
    # PowerShell output encoding can depend on the console code page. The
    # shortcut path is informational; uninstallation never relies on decoding it.
    return result.stdout.decode('utf-8', errors='replace').strip()


def run_checked(python: Path, args: list[str], cwd: Path, log, timeout: int = 90) -> None:
    print('检查：' + ' '.join(args), flush=True)
    log.write('\nCOMMAND ' + repr(args) + '\n'); log.flush()
    env = os.environ.copy()
    for key in ('PYTHONHOME', 'PYTHONPATH'):
        env.pop(key, None)
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    result = subprocess.run([str(python), '-B', *args], cwd=cwd, env=env,
                            stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    log.flush()
    if result.returncode:
        raise RuntimeError(f'本机检查失败（退出码 {result.returncode}）：{args}。请保留日志，不要把安装标为成功。')


def install(args) -> int:
    environment = prerequisites()
    local = Path(os.environ['LOCALAPPDATA']).resolve()
    root = local / 'HakimiBJTrial' / PACKAGE_ID
    data = local / 'HakimiBJTrialData' / PACKAGE_ID
    logs = local / 'HakimiBJTrialInstallLogs'
    for path in (root, data, logs):
        assert_no_links(path)
    if root.exists():
        raise FileExistsError(f'拒绝覆盖现有安装：{root}\n请使用原入口，或先运行该实例的 UNINSTALL_TRIAL.cmd；数据会保留。')
    if data.exists() and any(data.iterdir()) and not args.reuse_trial_data:
        raise FileExistsError(f'发现本试用版留下的数据：{data}\n不会自动覆盖或导入。确认继续使用同一试用数据后加 --reuse-trial-data。')
    print(TITLE + ' — 联网/源码型安装包（不是离线 EXE）')
    print('固定提交：' + COMMIT)
    print('安装目录：' + str(root))
    print('数据目录：' + str(data))
    print('不读取旧版数据库，不改 PATH，不修改系统 Python，不自动安装系统组件。')
    print('需保留此 Python 3.14 基础安装；本虚拟环境不能脱离它运行。')
    if not args.yes and input('输入 INSTALL 开始安装，其他输入取消：').strip() != 'INSTALL':
        print('已取消；没有安装应用。'); return 0
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / (datetime.now().strftime('install-%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8] + '.log')
    with log_path.open('x', encoding='utf-8', buffering=1) as log:
        log.write(json.dumps(environment, ensure_ascii=False, indent=2) + '\n')
        try:
            with tempfile.TemporaryDirectory(prefix='hakimi-trial-download-') as temp:
                bundled_source = HERE / f'source-{COMMIT[:7]}.zip'
                supplied = args.source_zip or (bundled_source if bundled_source.exists() else None)
                if supplied:
                    archive = Path(supplied).resolve()
                    print('校验本地固定源码包：' + str(archive))
                else:
                    archive = Path(temp) / 'source.zip'
                    print('正在下载固定提交源码；不使用 main 或分支最新值。', flush=True)
                    download_source(archive)
                manifest = inspect_archive(archive)
                print(f'完整 Git 源码树一致，共 {len(manifest["files"])} 个文件。')
                root.parent.mkdir(parents=True, exist_ok=True)
                root.mkdir(exist_ok=False)
                extract_verified(archive, root / 'app', manifest)
                manifest.pop('_members')
                write_json_new(root / 'source-manifest.json', manifest)
                for filename in ('trial_common.py', 'launch_trial.py', 'trial_tools.py', 'README_试用版.txt', 'CLOSEOUT_GUIDE.md', 'PACKAGE.json', 'VALIDATION.json', 'INSTALLER_TEST_LOG.txt'):
                    shutil.copy2(HERE / filename, root / filename)
                write_json_new(root / 'install.json', dict(
                    package_id=PACKAGE_ID, edition='TRIAL / 试用版', commit=COMMIT, git_tree=TREE,
                    install_root=str(root), data_root=str(data), base_python=environment['base_python'],
                    environment=environment, installer_log=str(log_path),
                    main_analysis=True, sidebet_research=False, real_time_accepted=False,
                    windows_installer_validated_before_delivery=False))
                uninstall_runner = logs / (PACKAGE_ID + '-uninstall-' + uuid.uuid4().hex[:8] + '.cmd')
                uninstall_batch(root / 'UNINSTALL_TRIAL.cmd', uninstall_runner, environment['base_python'])
                # Build at the final destination. venvs must not be moved later.
                venv.EnvBuilder(with_pip=False, system_site_packages=False, symlinks=False).create(root / 'runtime')
                python = root / 'runtime/Scripts/python.exe'
                run_checked(python, ['-m', 'blackjack_lab.main', '--check'], root / 'app', log)
                run_checked(python, ['-m', 'blackjack_lab.main', '--prepare-split'], root / 'app', log)
                run_checked(python, ['-m', 'blackjack_lab.main', '--check-environment'], root / 'app', log)
                run_checked(python, ['-c',
                    "import tkinter as tk; r=tk.Tk(); r.withdraw(); r.update(); r.destroy(); print('TK_WINDOW_SMOKE_OK')"], root / 'app', log)
                # Only a passed local preflight creates the success marker.
                batch_file(root / 'START_TRIAL.cmd', [
                    'cd /d "%~dp0"', '"%~dp0runtime\\Scripts\\python.exe" -I -B "%~dp0launch_trial.py"',
                    'set "CODE=%ERRORLEVEL%"', 'if not "%CODE%"=="0" pause', 'exit /b %CODE%'])
                for name, action in (('BACKUP_DATA.cmd', 'backup'), ('OPEN_DATA.cmd', 'open-data'), ('VERIFY_SOURCE.cmd', 'verify')):
                    batch_file(root / name, ['"%~dp0runtime\\Scripts\\python.exe" -B "%~dp0trial_tools.py" ' + action,
                                            'set "CODE=%ERRORLEVEL%"', 'pause', 'exit /b %CODE%'])
                write_json_new(root / 'INSTALL_SUCCESS.json', dict(
                    package_id=PACKAGE_ID, commit=COMMIT, local_preflight_passed=True,
                    native_ui_acceptance=False, real_time_acceptance=False,
                    archive_sha256=sha256_file(archive), completed_at=datetime.now().astimezone().isoformat()))
                shortcut = create_shortcut(root)
                log.write('SHORTCUT=' + repr(shortcut) + '\nINSTALL_PREFLIGHT_SUCCESS\n')
            print('\n安装和本机预检已通过；不代表 Windows 完整验收或实时验收通过。')
            print('启动：' + str(root / 'START_TRIAL.cmd'))
            print('日志：' + str(log_path))
            return 0
        except Exception:
            traceback.print_exc(file=log)
            print('安装失败。旧版和旧数据未被安装器修改。')
            print('日志：' + str(log_path))
            print('若已产生新试用安装目录，请保留失败材料；不要把它当作成功安装。')
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=TITLE + ' 独立联网安装器')
    parser.add_argument('--source-zip', type=Path, help='已下载的固定提交 ZIP；仍须通过完整 Git 树校验')
    parser.add_argument('--reuse-trial-data', action='store_true', help='明确复用同一个试用数据目录；不导入其他版本')
    parser.add_argument('--yes', action='store_true', help='明确跳过 INSTALL 提示')
    args = parser.parse_args()
    try:
        return install(args)
    except Exception as error:
        print('\n未完成安装：' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
