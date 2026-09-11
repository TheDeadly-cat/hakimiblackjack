"""Local, exact managed accelerator with request-owned Windows process lifetime.

The bundled C# source is built using the Windows .NET Framework compiler. No
packages, services or network are required. Build time is charged to a request
when an artifact has not already been prepared by the launcher/build command.
"""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from time import perf_counter

from .probability import CalculationStopped, InsufficientCards

SOURCE = Path(__file__).with_name("native") / "SplitEngine.cs"
FLAGS = ("/nologo", "/optimize+", "/r:System.Web.Extensions.dll")
NO_WINDOW = 0x08000000


def source_digest():
    return hashlib.sha256(SOURCE.read_bytes()).hexdigest()


def compiler_path():
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"


def native_cache_directory():
    return SOURCE.parents[2] / ".local-native" / source_digest()


def _run_owned_process(command, timeout, stdin_data=None):
    """Run a helper owned by a kill-on-close job so worker death cannot orphan it."""
    job = process = None
    try:
        job = _Job()
        process = subprocess.Popen(command, stdin=subprocess.PIPE if stdin_data is not None else None,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        job.assign(process)
        stdout, stderr = process.communicate(stdin_data, timeout=timeout)
        return process.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        raise
    finally:
        if job:
            job.close()
        if process:
            if process.poll() is None:
                process.kill()
            try:
                process.communicate()
            except Exception:
                pass


def _compile_source(frozen_source, output, timeout):
    compiler = compiler_path()
    if not compiler.is_file():
        raise RuntimeError("找不到 Windows .NET Framework 4 编译器，分牌分析不可用。"
                           f"期望路径：{compiler}。请启用该组件后运行 python scripts/check_environment.py；"
                           "不要自动安装或提权。录牌功能仍可使用。")
    code, stdout, stderr = _run_owned_process(
        [str(compiler), *FLAGS, "/out:" + str(output), str(frozen_source)], timeout)
    if code:
        raise RuntimeError("分牌加速器编译失败：" + (stdout or "") + (stderr or ""))


def build_native(timeout=15):
    """Serialize publication with an OS mutex, released even after process death."""
    if os.name != 'nt':
        raise RuntimeError('分牌加速器当前需要 Windows .NET Framework 4 编译器')
    started = perf_counter()
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    api.CreateMutexW.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    api.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    name = 'Local\\HakimiBlackjackBuild-' + hashlib.sha256(str(SOURCE.resolve()).encode('utf-8')).hexdigest()
    handle = api.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        status = api.WaitForSingleObject(handle, max(1, int(timeout*1000)))
        acquired = status in (0, 0x80)  # normal or abandoned owner
        remaining = timeout-(perf_counter()-started)
        if not acquired or remaining <= 0:
            raise subprocess.TimeoutExpired('native build lock', timeout)
        return _build_native_locked(remaining)
    finally:
        if acquired:
            api.ReleaseMutex(handle)
        api.CloseHandle(handle)


def _build_native_locked(timeout):
    if os.name != "nt":
        raise RuntimeError("分牌加速器当前需要 Windows .NET Framework 4 编译器")
    source_bytes = SOURCE.read_bytes()
    key = hashlib.sha256(source_bytes).hexdigest()
    directory = SOURCE.parents[2] / ".local-native" / key
    executable = directory / "SplitEngine.exe"
    receipt = directory / "build.json"
    if executable.is_file() and receipt.is_file():
        saved = json.loads(receipt.read_text(encoding="utf-8"))
        if (saved.get("source_sha256") == key and saved.get("flags") == list(FLAGS)
                and saved.get("binary_sha256") == hashlib.sha256(executable.read_bytes()).hexdigest()):
            return executable
        raise RuntimeError("本地分牌加速器摘要不匹配；请保留该损坏目录并在旁边重新准备新构建，"
                           "不要删除用户数据库或分析快照。")
    write_error = f"无法写入分牌数值程序目录 {directory}；请检查权限。不要为此自动安装软件或提权。录牌功能仍可使用。"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise RuntimeError(write_error) from error
    # Concurrent preparations build unique files; publication replaces only a
    # derived artifact for this identical source identity, never user data.
    try:
        with tempfile.TemporaryDirectory(prefix="build-", dir=directory) as temporary:
            output = Path(temporary) / "SplitEngine.exe"
            frozen_source = Path(temporary) / "SplitEngine.cs"
            frozen_source.write_bytes(source_bytes)
            _compile_source(frozen_source, output, timeout)
            metadata = dict(source_sha256=key, binary_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                            compiler=str(compiler_path()), flags=FLAGS)
            temp_receipt = Path(temporary) / "build.json"
            temp_receipt.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            os.replace(output, executable)
            os.replace(temp_receipt, receipt)
    except OSError as error:
        raise RuntimeError(write_error) from error
    return executable


class _Job:
    """Closing the worker's non-inheritable handle kills its accelerator only."""
    def __init__(self):
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_set", ctypes.c_size_t),
                        ("max_set", ctypes.c_size_t), ("active", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("r", "w", "o", "rb", "wb", "ob")]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", IO)] + [(name, ctypes.c_size_t) for name in
                        ("process_memory", "job_memory", "peak_process", "peak_job")]
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        self.api.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        self.api.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def solve_native(counts, hands, dealer_up, peek_negative, *, active=0,
                 split_aces=False, force_active=False, budget_seconds=5.0,
                 _single_player=None, _single_actions=()):
    import math
    start = perf_counter()
    if (len(counts) != 10 or any(type(n) is not int or not 0 <= n <= (128 if i == 9 else 32) for i, n in enumerate(counts))
            or len(hands) != 2 or any(not h or any(type(v) is not int or not 1 <= v <= 10 for v in h) for h in hands)
            or type(active) is not int or active not in (0, 1, 2)
            or type(dealer_up) is not int or not 1 <= dealer_up <= 10
            or any(type(v) is not bool for v in (peek_negative, split_aces, force_active))
            or type(budget_seconds) not in (int, float) or not math.isfinite(budget_seconds) or not 0 < budget_seconds <= 5):
        raise ValueError("无效的两手共享牌靴输入或计算预算")
    if active == 0 and len(hands[1]) != 1:
        raise ValueError("首手完成前不能提前收到第二手的新牌")
    process = job = None
    try:
        executable = build_native(timeout=budget_seconds)
        binary_digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        remaining = budget_seconds - (perf_counter() - start)
        if remaining <= 0:
            raise CalculationStopped("TIMEOUT")
        job = _Job()
        process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, creationflags=NO_WINDOW)
        # It waits for complete JSON on stdin. If this worker dies before job
        # assignment, pipe EOF exits parsing instead of starting computation.
        job.assign(process)
        remaining = budget_seconds - (perf_counter() - start)
        if remaining <= 0:
            raise CalculationStopped("TIMEOUT")
        payload = dict(counts=counts, hands=hands, dealer_up=dealer_up, peek_negative=peek_negative,
                       active=active, split_aces=split_aces, force_active=force_active,
                       budget_seconds=remaining)
        if _single_player is not None:
            payload.update(single_player=_single_player, single_actions=_single_actions)
        stdout, stderr = process.communicate(json.dumps(payload), timeout=remaining)
        data = json.loads(stdout)
        if data.get("status") != "available":
            if data.get("error") == "TIMEOUT":
                raise CalculationStopped("TIMEOUT")
            if data.get("error") == "INSUFFICIENT_CARDS":
                raise InsufficientCards("剩余物理牌不足以完整评估所有相关分支")
            raise RuntimeError(data.get("error", stderr or "加速器没有完整结果"))
        if process.returncode or perf_counter() - start >= budget_seconds:
            raise CalculationStopped("TIMEOUT")
        data.update(backend="windows-dotnet-framework-exact", backend_source_sha256=executable.parent.name,
                    backend_binary_sha256=binary_digest,
                    method="exact_finite_shared_shoe_float64", approximation=False,
                    strategy="sequential-two-hand-total-net-hit-stand-v1", decision_tolerance=1e-12,
                    elapsed_seconds=perf_counter()-start)
        return data
    except subprocess.TimeoutExpired as error:
        raise CalculationStopped("TIMEOUT") from error
    finally:
        if job:
            job.close()
        if process:
            if process.poll() is None:
                process.kill()
            process.communicate()


def solve_presplit_native(counts, player, dealer_up, peek_negative, actions, budget_seconds=5.0):
    if (len(player)<2 or any(type(v) is not int or not 1<=v<=10 for v in player)
            or any(a not in ('stand','hit','double','split','surrender') for a in actions)
            or len(set(actions))!=len(actions)):
        raise ValueError('无效的分牌前手牌或动作')
    if 'split' in actions and (len(player)!=2 or player[0]!=player[1]):
        raise ValueError('分牌动作需要已确定的两张同牌面对；输入入口另验证原始牌面')
    return solve_native(counts, ((player[0],),(player[1],)), dealer_up, peek_negative,
                        split_aces=player[0]==1 and player[1]==1, budget_seconds=budget_seconds,
                        _single_player=player, _single_actions=actions)
