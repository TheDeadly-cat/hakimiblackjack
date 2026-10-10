"""Frozen trial launcher; preserves the existing point-entry scope and budgets."""
import argparse
import contextlib
import ctypes
from datetime import datetime
import json
import multiprocessing
import os
from pathlib import Path
import sys
import uuid
import time
import traceback

from offline_common import COMMIT, PACKAGE_ID, TITLE, installation_data, program_root, verify_bundle
from trial_common import assert_no_links, instance_lock, write_json_new


def preflight(receipt_path):
    receipt_path = Path(receipt_path).resolve()
    assert_no_links(receipt_path)
    if receipt_path.exists():
        raise ValueError('预检结果文件已经存在；保留原结果，不覆盖。')
    result = dict(package_id=PACKAGE_ID, commit=COMMIT, frozen=bool(getattr(sys, 'frozen', False)),
        executable=sys.executable, python_version=sys.version, ordinary_permissions_test=False,
        native_keyboard_test=False, clean_no_python_host_test=False, network_disabled_host_test=False,
        realtime_accepted=False)
    root = program_root()
    try:
        verify_bundle(root)
        result['complete_bundle_and_source_verified'] = True
        from blackjack_lab.analysis.environment import inspect_environment
        environment = inspect_environment()
        result['environment'] = environment
        if not environment['ready']:
            raise RuntimeError('冻结环境检查未通过')
        from blackjack_lab.analysis.split_contracts import both_initial_das_rules
        from blackjack_lab.ledger.events import SOURCE_SIMULATOR
        from blackjack_lab.ui.controller import SessionController
        from blackjack_lab.ui.read_snapshot import PrefixSnapshot
        from blackjack_lab.ui.recording_owner import RecordingOwner
        owned = receipt_path.parent / ('frozen-preflight-' + uuid.uuid4().hex)
        owned.mkdir(parents=True, exist_ok=False)
        result['preserved_synthetic_fixture_directory'] = str(owned)
        with contextlib.nullcontext():
            database = owned / 'synthetic.db'
            controller = SessionController(database, recording_source=SOURCE_SIMULATOR)
            owner = None
            try:
                controller.new_shoe(both_initial_das_rules(8))
                controller.start_round(['玩家1'], simple_hole=True)
                base = PrefixSnapshot.capture(controller.ledger)
                owner = RecordingOwner(database, SOURCE_SIMULATOR, use_process=True)
                requests = [owner.submit(base, 'deal_shown', '玩家1', '8'),
                    owner.submit(base, 'deal_shown', '庄家', '6')]
                owner.close()
                receipts = []
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    receipts.extend(owner.poll())
                    if owner.stopped and owner.pending_count == 0:
                        break
                    time.sleep(.01)
                if not owner.stopped or len(receipts) != 2:
                    raise RuntimeError('冻结录牌进程未完成有序关闭')
                if ([item.request_id for item in receipts] != requests
                        or any(item.status != 'committed' for item in receipts)
                        or receipts[0].after != receipts[1].before):
                    raise RuntimeError('冻结录牌请求或回执不符')
                actual = controller.store.load_ledger(controller.session_id)
                if PrefixSnapshot.capture(actual) != receipts[-1].after:
                    raise RuntimeError('冻结进程回执与耐久资料不符')
                cards = [(event.payload['seat'], event.payload['rank']) for event in actual.events
                    if event.etype == 'CARD_DEALT']
                if cards != [('玩家1', '8'), ('庄家', '6')]:
                    raise RuntimeError('冻结进程录牌顺序不符')
                owner.release_resources()
                result['frozen_production_recording_spawn_FIFO_durable_and_close_verified'] = True
                result['synthetic_cards'] = cards
            finally:
                if owner is not None:
                    owner.close()
                controller.close()
        verify_bundle(root)
        result['bundle_unchanged'] = True
        result['preflight_passed'] = True
        code = 0
    except Exception:
        result['preflight_passed'] = False
        result['error'] = traceback.format_exc()
        code = 1
    write_json_new(Path(receipt_path), result)
    return code


def show_error(message):
    try:
        import tkinter as tk
        from tkinter import messagebox
        dialog = tk.Tk()
        dialog.withdraw()
        messagebox.showerror(TITLE, message, parent=dialog)
        dialog.destroy()
    except Exception:
        if sys.stderr is not None:
            print(message, file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--maintenance', choices=('verify', 'backup', 'prepare-uninstall', 'uninstall'))
    parser.add_argument('--yes', action='store_true')
    args = parser.parse_args()
    if args.preflight:
        if args.receipt is None:
            raise ValueError('预检必须使用新的结果文件路径')
        return preflight(args.receipt)
    if args.maintenance:
        from offline_tools import maintain
        return maintain(args.maintenance, yes=args.yes, receipt_path=args.receipt)
    try:
        root = program_root()
        verify_bundle(root)
        info = json.loads((root / 'install.json').read_text(encoding='utf-8'))
        expected_data = installation_data(root, info)
        success = json.loads((root / 'INSTALL_SUCCESS.json').read_text(encoding='utf-8'))
        if success.get('package_id') != PACKAGE_ID or success.get('preflight_passed') is not True:
            raise ValueError('离线安装预检尚未通过')
        data = expected_data
        assert_no_links(data)
        with instance_lock(data):
            logs = data / 'logs'
            logs.mkdir(exist_ok=True)
            log = logs / datetime.now().strftime('offline-trial-%Y%m%d-%H%M%S-%f.log')
            with log.open('x', encoding='utf-8', buffering=1) as stream:
                with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                    print(f'EDITION=TRIAL PACKAGE_ID={PACKAGE_ID} SOURCE={COMMIT}', flush=True)
                    try:
                        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Hakimi.Blackjack.Trial.T1O1.3ec2006')
                        from blackjack_lab.analysis.native_backend import build_native
                        build_native()
                        from blackjack_lab.ui.app import BlackjackLabApp
                        class TrialApp(BlackjackLabApp):
                            def title(self, string=None):
                                if string is None:
                                    return super().title()
                                prefix = f'{TITLE} · {COMMIT[:7]} · '
                                caption = str(string)
                                return super().title(caption if caption.startswith(prefix) else prefix + caption)
                            wm_title = title
                        app = TrialApp(data / 'session.db', auto_analysis=True,
                            background_recording=True, recording_process=True, sidebet_research=False)
                        from tkinter import messagebox
                        messagebox.showwarning(TITLE,
                            '这是点值记录与复核试用版，实时性能仍未通过验收。\n\n'
                            '主注自动分析开启；日常 Perfect Pairs / 21+3 研究停用。\n'
                            '待保存或结果未知时须核对原资料，不自动重试同一张牌。\n'
                            f'试用资料目录：{data}', parent=app)
                        app.mainloop()
                    except Exception:
                        traceback.print_exc()
                        show_error(f'试用启动或运行失败；资料保留。\n日志：{log}\n\n' + traceback.format_exc()[-1600:])
                        return 1
        return 0
    except Exception:
        show_error(traceback.format_exc()[-1600:])
        return 1


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
