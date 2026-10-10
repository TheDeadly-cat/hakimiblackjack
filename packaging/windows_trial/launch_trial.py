"""Separate TRIAL launcher. Application source and mathematical code stay unchanged."""
from __future__ import annotations
import contextlib
from datetime import datetime
import json
import multiprocessing
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parent
# Spawned Python workers also need the pinned application's import root.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'app'))
from trial_common import COMMIT, PACKAGE_ID, TITLE, instance_lock, load_install, verify_installed_source


def show_error(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox
        dialog = tk.Tk(); dialog.withdraw()
        messagebox.showerror(TITLE, message, parent=dialog)
        dialog.destroy()
    except Exception:
        if sys.stderr is not None:
            print(message, file=sys.stderr)


def main() -> int:
    try:
        if os.name != 'nt':
            raise RuntimeError('此安装入口面向 Windows x64。')
        info = load_install(ROOT)
        data = Path(info['data_root'])
        with instance_lock(data):
            logs = data / 'logs'; logs.mkdir(exist_ok=True)
            log = logs / (datetime.now().strftime('trial-%Y%m%d-%H%M%S-%f') + '.log')
            with log.open('x', encoding='utf-8', buffering=1) as stream:
                with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                    print(f'EDITION=TRIAL PACKAGE_ID={PACKAGE_ID} SOURCE={COMMIT}', flush=True)
                    print(f'DATA_DIRECTORY={data}', flush=True)
                    try:
                        manifest = json.loads((ROOT / 'source-manifest.json').read_text(encoding='utf-8'))
                        verify_installed_source(ROOT / 'app', manifest)
                        os.chdir(ROOT / 'app')
                        from blackjack_lab.analysis.native_backend import build_native
                        build_native()  # Reuse only a source/digest-verified native artifact.
                        from blackjack_lab.ui.app import BlackjackLabApp
                        app = BlackjackLabApp(data / 'session.db', auto_analysis=True,
                            background_recording=True, recording_process=True, sidebet_research=False)
                        upstream_title = app.title()
                        app.title(f'{TITLE} · {COMMIT[:7]} · {upstream_title}')
                        from tkinter import messagebox
                        messagebox.showwarning(TITLE,
                            '这是点值记录与复核试用版，不是已通过实时性能验收的正式版。\n\n'
                            '输入：A–9、T；主注自动分析保留；Perfect Pairs / 21+3 自动研究停用。\n'
                            '大历史连续录牌可能明显延迟；“待保存 / 需核对”时不要依赖旧判断。\n'
                            '程序不保证收益，不自动下注，不读取旧版数据。\n\n'
                            f'试用资料目录：{data}\n关闭时请等待原程序完成保存。', parent=app)
                        app.mainloop()
                    except Exception:
                        traceback.print_exc()
                        show_error(f'试用版启动或运行失败；原资料保留。\n日志：{log}\n\n' + traceback.format_exc()[-1600:])
                        return 1
        return 0
    except Exception as error:
        show_error(str(error))
        return 1


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
