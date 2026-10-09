"""Cooperative GUI exit: drain writes, retain failed results, never join on Tk."""
import threading
from pathlib import Path
import tkinter as tk
from tkinter import messagebox,ttk

from ..analysis.sidebets.background import LatestWorker


class ExitFlow:
    def __init__(self,app):
        self.app=app
        self.window=None
        self.phase='starting'
        self.return_requested=False
        self.errors=[]
        self.thread=None
        self.workers=[]
        self.drain_workers=[]
        self.readers=[]
        self.processes=[]
        self.side_history=self.opening_history=None
        self.main_history=None
        self.recording_acknowledged=False
        self.result_writers=[]

    def show(self):
        if self.window is not None and self.window.winfo_exists():self.window.lift()

    def start(self):
        app=self.app;app._closing=True
        if app._recording_owner:
            app._recording_owner.close()
        if app.opening_estimate.writer:
            self.result_writers.append(app.opening_estimate.writer)
            app.opening_estimate.writer.close()
        if app.opening_estimate.reaper:
            self.result_writers.append(app.opening_estimate.reaper)
            app.opening_estimate.reaper.close()
        self.window=tk.Toplevel(app);self.window.title('安全退出')
        self.window.transient(app);self.window.geometry('560x235');self.window.resizable(False,False)
        self.message=tk.StringVar(value='正在保存已接收的录牌输入并结束后台任务；请等待保存回执。')
        ttk.Label(self.window,textvariable=self.message,wraplength=510).pack(fill=tk.X,padx=20,pady=20)
        ttk.Label(self.window,text='尚未完成的当前计算会取消；明确请求的边注复算和已开始的保存会等待完成。',
                  wraplength=510).pack(fill=tk.X,padx=20)
        bar=ttk.Frame(self.window);bar.pack(padx=12,pady=18)
        self.retry_button=ttk.Button(bar,text='重试保存并退出',command=self.retry)
        self.retry_button.pack(side=tk.LEFT,padx=4);self.retry_button.state(['disabled'])
        ttk.Button(bar,text='返回应用',command=self.return_to_app).pack(side=tk.LEFT,padx=4)
        self.discard_button=ttk.Button(bar,text='放弃未保存结果并退出',command=self.discard)
        self.discard_button.pack(side=tk.LEFT,padx=4);self.discard_button.state(['disabled'])
        self.window.protocol('WM_DELETE_WINDOW',self.return_to_app)
        self.window.grab_set()
        panel=app.analysis_panel;panel._cancel_auto()
        services=[panel.service,panel.overview.service,app.opening_estimate.service]
        cancellations=[]
        record=app.opening_estimate.queue_exit_cancellation()
        if record:cancellations.append(record)
        self.workers=[app.sidebets.worker]
        h=getattr(panel,'history',None)
        if h is not None and h.winfo_exists():
            self.main_history=h;self.workers.append(h.worker)
        h=app.sidebets.history
        if h is not None and h.winfo_exists():
            self.side_history=h;self.workers.append(h.worker)
        h=app.opening_estimate.history
        if h is not None and h.winfo_exists():
            self.opening_history=h;services.append(h.service)
            if hasattr(h,'worker'):self.workers.append(h.worker)
            h._load_cancel.set();self.readers=list(h._load_threads)
            if getattr(h,'_close_thread',None):self.readers.append(h._close_thread)
            record=app.opening_estimate.queue_exit_cancellation(h)
            if record:cancellations.append(record)
        self.processes=[service.active['process'] for service in services
                        if service.active is not None and service.active.get('process') is not None]
        for worker in self.workers:
            # User-requested writes/recomputations are drained, not latest-wins work.
            if (worker.active_channel in ('history_recompute','retry')
                    or any(k in ('history_recompute','retry') for k in worker.pending)):
                self.drain_workers.append(worker)
            else:worker.close()
        def stop():
            for service in services:
                try:service.close()
                except Exception as error:self.errors.append(str(error))
            for record in cancellations:app.opening_estimate.write_pending_record(record)
        self.phase='draining'
        self.thread=threading.Thread(target=stop,daemon=True,name='application-exit')
        self.thread.start();self.poll()

    def pending(self):
        with self.app.sidebets._lock:side=len(self.app.sidebets.pending_saves)
        return dict(main=len(self.app.pending_analysis),opening=len(self.app.opening_estimate.pending_saves),sidebets=side)

    def poll(self):
        for worker in self.drain_workers:
            if worker.active is None and not worker.pending:worker.close()
        cleanup_busy=self.thread is not None and self.thread.is_alive()
        process_busy=False
        if not cleanup_busy:
            for process in self.processes:
                try:process_busy=process.is_alive() or process_busy
                except ValueError:pass  # Service already joined and closed this handle.
        busy=(process_busy or cleanup_busy
              or any(not w.stopped for w in self.workers) or any(t.is_alive() for t in self.readers)
              or self.app.recording_busy
              or any(not w.stopped for w in self.result_writers)
              or bool(self.app._recording_owner and not self.app._recording_owner.stopped))
        if busy:
            self.app.after(25,self.poll);return
        for process in self.processes:
            try:process.close()
            except ValueError:pass
        if self.return_requested:self.resume();return
        if self.app._recording_faults and not self.recording_acknowledged:
            self.phase='needs_recording'
            self.message.set('录牌保存有待核对输入，未自动重试。请返回应用读取已保存记录。\n'
                             + self.app.recording_fault_message())
            self.retry_button.state(['disabled'])
            self.discard_button.configure(text='保留待核对输入并退出')
            retained=all(f.get('failure_path') and Path(f['failure_path']).is_file()
                         for f in self.app._recording_faults)
            self.discard_button.state(['!disabled'] if retained else ['disabled'])
            return
        counts=self.pending();total=sum(counts.values())
        if not total and not self.errors:self.finish();return
        self.phase='needs_save'
        self.message.set(f"尚有 {total} 条结果未保存：主注 {counts['main']}、开局 {counts['opening']}、边注 {counts['sidebets']}。"
                         +'牌面数据库已提交；可以重试、返回继续处理，或明确放弃这些结果。'
                         +(' 后台结束需核对：'+'；'.join(self.errors) if self.errors else ''))
        self.retry_button.state(['!disabled'])
        self.discard_button.configure(text='放弃未保存结果并退出')
        if not self.errors:self.discard_button.state(['!disabled'])

    def retry(self):
        if self.phase!='needs_save':return
        self.phase='retrying';self.retry_button.state(['disabled']);self.discard_button.state(['disabled'])
        self.message.set('正在重试原始结果的保存；窗口仍可响应，请等待。')
        app=self.app
        def save():
            for key,record in list(app.pending_analysis.items()):
                try:
                    saved=record['store'].save(record['result'],record['recomputed_from'])
                    app.pending_analysis.pop(key,None)
                    if app.analysis_panel.last_result and app.analysis_panel.last_result['request_id']==key:
                        app.analysis_panel.saved=saved
                except Exception:pass  # The original pending record remains for another attempt or explicit discard.
            for record in list(app.opening_estimate.pending_saves):
                saved=app.opening_estimate.write_pending_record(record)
                if saved and app.opening_estimate.result and record['result']['request_id']==app.opening_estimate.result['request_id']:
                    app.opening_estimate.saved=saved
            with app.sidebets._lock:records=list(app.sidebets.pending_saves.values())
            for record in records:app.sidebets.save_record(record)
        self.thread=threading.Thread(target=save,daemon=True,name='exit-save-retry')
        self.thread.start();self.poll()

    def return_to_app(self):
        self.return_requested=True
        if self.phase in ('needs_save','needs_recording'):self.resume()
        else:self.message.set('正在结束本次后台操作；完成后返回应用，未保存结果仍保留。')

    def resume(self):
        app=self.app
        app.sidebets.worker=LatestWorker();app.sidebets.key=None
        app.sidebets.observation_key=None
        h=self.side_history
        if h is not None and h.winfo_exists() and not h.closed:
            h.worker=LatestWorker();h.expected.clear();h.loading=h.recomputing=False;h.verified=None
        app.opening_estimate.key=None
        app.analysis_panel.context_key=None
        app.opening_estimate.writer=None
        app.opening_estimate.reaper=None
        if app._recording_owner and not app._recording_faults:
            app._recording_owner.release_resources()
            app._recording_owner=None
        app._closing=False;app.exit_flow=None
        self.window.grab_release();self.window.destroy();self.phase='returned'
        app.refresh_all()
        main=self.main_history
        if main is not None and main.winfo_exists() and not main.closed:
            main.worker=LatestWorker();main.expected.clear();main.reload()
        if h is not None and h.winfo_exists() and not h.closed:h.reload()
        h=self.opening_history
        if h is not None and h.winfo_exists() and not h._closed:
            h.worker=LatestWorker();h.expected.clear();h._load_thread=h.worker.thread;h._load_threads=[h.worker.thread]
            h.reload()

    def discard(self):
        if self.phase=='needs_recording':
            retained=all(f.get('failure_path') and Path(f['failure_path']).is_file()
                         for f in self.app._recording_faults)
            if retained and messagebox.askyesno('保留待核对录牌并退出',
                    '原输入与错误已保存在数据库旁的 recording-failures 文件夹。\n'
                    '这些输入没有自动重试；保存结果待核对的牌也不能重复录入。\n\n'
                    '保留核对记录并继续退出？', parent=self.window):
                self.recording_acknowledged=True
                self.phase='draining'
                self.discard_button.state(['disabled'])
                self.poll()
            return
        if self.phase!='needs_save' or self.errors:return
        total=sum(self.pending().values())
        if messagebox.askyesno('明确放弃未保存结果',f'放弃这 {total} 条尚未保存的分析结果并退出？\n已提交的牌面记录及已有历史文件不会删除。',parent=self.window):
            self.finish()

    def finish(self):
        self.phase='finished'
        self.window.grab_release();self.window.destroy()
        for history in (self.side_history,self.opening_history,self.main_history):
            if history is not None and history.winfo_exists():history.close()
        self.app._finalize_close()
