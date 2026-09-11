"""Windows split-accelerator precheck. Never installs software or elevates."""
import hashlib
import json
import os
import platform
import sqlite3
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from time import perf_counter

from . import native_backend as backend
from .native_backend import BuildReceiptError, parse_build_receipt


def inspect_environment(*, prepare=False):
    failures = []
    warnings = []
    bits = struct.calcsize("P") * 8
    compiler = backend.compiler_path()
    cache = backend.native_cache_directory()
    executable = cache / "SplitEngine.exe"
    receipt = cache / "build.json"
    tk_version = None
    tcl_patch = None
    compiler_banner = None
    artifact = None
    writable = False
    write_error = None
    prepare_seconds = None

    if os.name != "nt":
        failures.append(_issue("NOT_WINDOWS", "分牌数值程序需要 Windows",
                               "在 Windows 64 位目标机上运行；不要为了本机方便改成 mock 或删除测试"))
    if bits != 64:
        failures.append(_issue("NOT_64BIT", f"当前 Python 为 {bits} 位，产品要求 64 位",
                               "使用 64 位 Python；不要自动更换解释器或提权"))

    try:
        import tkinter
        tk_version = tkinter.TkVersion
        root = tkinter.Tk()
        try:
            tcl_patch = root.tk.eval("info patchlevel")
        finally:
            root.destroy()
    except Exception as error:
        failures.append(_issue("TK_UNAVAILABLE", "无法初始化 Tkinter：" + str(error),
                               "安装带 Tcl/Tk 的官方 Python；不要改用 Web 界面"))

    if compiler.is_file():
        try:
            help_result = subprocess.run([str(compiler)], capture_output=True, text=True,
                                         encoding="utf-8", errors="replace",
                                         timeout=8, creationflags=backend.NO_WINDOW)
            compiler_banner = (help_result.stdout or help_result.stderr or "").strip().splitlines()[:2]
        except Exception as error:
            warnings.append(_issue("COMPILER_BANNER", "已找到编译器但无法读取版本：" + str(error),
                                   "确认 .NET Framework 4 可用；不要自动安装"))
    else:
        message = f"找不到编译器 {compiler}"
        action = "启用 Windows .NET Framework 4 后运行 python scripts/check_environment.py；不要自动安装、改执行策略或要求管理员启动"
        if not (executable.is_file() and receipt.is_file()):
            failures.append(_issue("COMPILER_MISSING", message + "，且没有已校验的分牌产物", action))
        else:
            warnings.append(_issue("COMPILER_MISSING", message + "；若现有产物摘要正确仍可计算，但不能重新编译", action))

    probe = None
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        handle, probe_path = tempfile.mkstemp(prefix=".hakimi-write-", suffix=".tmp", dir=str(cache.parent))
        os.close(handle)
        probe = Path(probe_path)
        probe.write_text("ok", encoding="utf-8")
        writable = True
    except OSError as error:
        writable = False
        write_error = str(error)
        failures.append(_issue("CACHE_NOT_WRITABLE", f"无法写入 {cache.parent}：{error}",
                               "检查目录权限。不要删除 data/ 或 .analysis；不要提权启动作为常规方案"))
    finally:
        if probe is not None:
            try:
                probe.unlink()
            except OSError:
                pass

    if executable.is_file() and receipt.is_file():
        try:
            saved = parse_build_receipt(receipt.read_text(encoding="utf-8"))
            binary = hashlib.sha256(executable.read_bytes()).hexdigest()
            source = backend.source_digest()
            match = (saved["source_sha256"] == source and saved["flags"] == list(backend.FLAGS)
                     and saved["binary_sha256"] == binary and executable.parent.name == source)
            artifact = dict(path=str(executable), source_sha256=source,
                            receipt_source_sha256=saved["source_sha256"],
                            binary_sha256=binary, receipt_binary_sha256=saved["binary_sha256"],
                            flags=saved["flags"], valid=match)
            if not match:
                failures.append(_issue("ARTIFACT_HASH_MISMATCH",
                                       "本地分牌加速器摘要不匹配；请保留该损坏目录并在旁边重新准备",
                                       "不要删除用户数据库或历史快照，不要把清缓存扩大到 data/"))
        except (OSError, UnicodeError, BuildReceiptError) as error:
            failures.append(_issue("ARTIFACT_UNREADABLE", "无法读取构建回执：" + str(error),
                                   "保留损坏文件作诊断，使用新的源码摘要目录重新 --prepare-split"))
    elif compiler.is_file() and writable:
        warnings.append(_issue("ARTIFACT_ABSENT", "尚未准备当前源码的分牌数值程序",
                               "运行 python -m blackjack_lab.main --prepare-split；首次编译计入请求预算"))

    if prepare:
        started = perf_counter()
        try:
            built = backend.build_native()
            prepare_seconds = perf_counter() - started
            artifact = dict(path=str(built), source_sha256=backend.source_digest(),
                            binary_sha256=hashlib.sha256(built.read_bytes()).hexdigest(),
                            valid=True, prepared_now=True)
        except Exception as error:
            prepare_seconds = perf_counter() - started
            failures.append(_issue("PREPARE_FAILED", str(error),
                                   "保留损坏产物和用户数据；用 python scripts/check_environment.py 查看依赖，不要自动安装"))

    report = dict(
        ready=not failures,
        python_executable=sys.executable,
        python_version=sys.version,
        bits=bits,
        platform=platform.platform(),
        sqlite_version=sqlite3.sqlite_version,
        tk_version=tk_version,
        tcl_patch=tcl_patch,
        compiler=str(compiler),
        compiler_present=compiler.is_file(),
        compiler_banner=compiler_banner,
        native_source=str(backend.SOURCE.resolve()),
        native_cache=str(cache),
        cache_writable=writable,
        cache_write_error=write_error,
        artifact=artifact,
        can_rebuild=compiler.is_file() and writable and not any(item["code"] == "ARTIFACT_HASH_MISMATCH" for item in failures),
        prepare_seconds=prepare_seconds,
        installs_software=False,
        changes_execution_policy=False,
        requires_elevation=False,
        failures=failures,
        warnings=warnings,
    )
    return report


def format_report(report):
    lines = [
        f"ready={report['ready']}",
        f"python={report['python_executable']}",
        f"version={report['python_version'].splitlines()[0]}",
        f"bits={report['bits']} platform={report['platform']}",
        f"tk={report['tk_version']} tcl={report['tcl_patch']} sqlite={report['sqlite_version']}",
        f"compiler={report['compiler']} present={report['compiler_present']}",
    ]
    if report["compiler_banner"]:
        lines.append("compiler_banner=" + " | ".join(report["compiler_banner"]))
    lines.append(f"cache={report['native_cache']} writable={report['cache_writable']}")
    lines.append(f"artifact={json.dumps(report['artifact'], ensure_ascii=False)}")
    if report["prepare_seconds"] is not None:
        lines.append(f"prepare_seconds={report['prepare_seconds']:.3f}")
    for label, items in (("FAIL", report["failures"]), ("WARN", report["warnings"])):
        for item in items:
            lines.append(f"{label} {item['code']}: {item['message']}")
            lines.append(f"  action: {item['action']}")
    lines.append("installs_software=false changes_execution_policy=false requires_elevation=false")
    return "\n".join(lines)


def _issue(code, message, action):
    return dict(code=code, message=message, action=action)
