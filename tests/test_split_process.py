"""Accelerator artifact and OS lifetime boundaries, including killed workers."""
import ctypes
from ctypes import wintypes
import json
import multiprocessing as mp
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import hashlib
import threading

from blackjack_lab.analysis.native_backend import _Job, NO_WINDOW, build_native, source_digest


def _run_native_probe(marker):
    from blackjack_lab.analysis import native_backend as backend
    original=backend._Job.assign
    def record(job,process):
        original(job,process)
        Path(marker).write_text(str(process.pid),encoding='ascii')
    backend._Job.assign=record
    counts=[24]*9+[96]
    counts[1]-=3
    backend.solve_native(counts,((2,),(2,)),2,False)


class TestSplitProcess(unittest.TestCase):
    def test_concurrent_preparations_publish_one_consistent_artifact(self):
        from blackjack_lab.analysis import native_backend as backend
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'lab/analysis/native/SplitEngine.cs'
            source.parent.mkdir(parents=True)
            source.write_bytes(backend.SOURCE.read_bytes())
            barrier=threading.Barrier(4)
            def prepare(_):
                barrier.wait()
                return backend.build_native()
            with patch.object(backend,'SOURCE',source),ThreadPoolExecutor(max_workers=4) as pool:
                outputs=list(pool.map(prepare,range(4)))
            self.assertEqual(len(set(outputs)),1)
            metadata=json.loads(outputs[0].with_name('build.json').read_text(encoding='utf-8'))
            self.assertEqual(metadata['binary_sha256'],hashlib.sha256(outputs[0].read_bytes()).hexdigest())

    def test_source_edit_during_build_cannot_relabel_the_completed_result(self):
        from blackjack_lab.analysis import native_backend as backend
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'lab/analysis/native/SplitEngine.cs'
            source.parent.mkdir(parents=True)
            original=backend.SOURCE.read_bytes()
            source.write_bytes(original)
            run=subprocess.run
            def edit_after_capture(command,**kwargs):
                self.assertEqual(Path(command[-1]).read_bytes(),original)
                source.write_bytes(original+b'\n// edited after source capture\n')
                return run(command,**kwargs)
            with patch.object(backend,'SOURCE',source),patch.object(backend.subprocess,'run',side_effect=edit_after_capture):
                result=backend.solve_native((0,)*9+(6,),((8,),(8,)),6,False)
                self.assertEqual(result['backend_source_sha256'],hashlib.sha256(original).hexdigest())
                self.assertNotEqual(result['backend_source_sha256'],backend.source_digest())
                self.assertEqual(result['actions']['deal']['ev'],2.)

    def test_source_addressed_compiled_artifact_is_verified(self):
        executable=build_native()
        info=json.loads(executable.with_name('build.json').read_text(encoding='utf-8'))
        self.assertEqual(info['source_sha256'],source_digest())
        self.assertEqual(executable.parent.name,source_digest())
        self.assertEqual(build_native(),executable)

    def test_job_close_terminates_assigned_child_and_keeps_other_process(self):
        children=[]
        job=_Job()
        try:
            for _ in range(2):
                children.append(subprocess.Popen([sys.executable,'-c','import sys; sys.stdin.read()'],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=NO_WINDOW))
            job.assign(children[0])
            job.close()
            children[0].wait(timeout=2)
            self.assertIsNone(children[1].poll())
        finally:
            job.close()
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.communicate()

    def test_abrupt_python_worker_death_does_not_orphan_running_accelerator(self):
        build_native()
        with tempfile.TemporaryDirectory() as directory:
            marker=Path(directory)/'owned-child.txt'
            worker=mp.get_context('spawn').Process(target=_run_native_probe,args=(str(marker),))
            handle=None
            api=ctypes.WinDLL('kernel32',use_last_error=True)
            api.OpenProcess.argtypes=(wintypes.DWORD,wintypes.BOOL,wintypes.DWORD)
            api.OpenProcess.restype=wintypes.HANDLE
            api.WaitForSingleObject.argtypes=(wintypes.HANDLE,wintypes.DWORD)
            api.CloseHandle.argtypes=(wintypes.HANDLE,)
            try:
                worker.start()
                deadline=time.perf_counter()+3
                while (not marker.exists() or not marker.read_text(encoding='ascii').strip()) and time.perf_counter()<deadline:
                    time.sleep(.01)
                self.assertTrue(marker.exists(),'Accelerator never reached job assignment')
                pid=int(marker.read_text(encoding='ascii'))
                handle=api.OpenProcess(0x00100000,False,pid)  # SYNCHRONIZE only, own recorded child.
                self.assertTrue(handle)
                time.sleep(.2)
                self.assertEqual(api.WaitForSingleObject(handle,0),0x102,'Probe must still be running before cancellation')
                worker.terminate()
                worker.join(timeout=2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(api.WaitForSingleObject(handle,2000),0,'Killed worker left a live accelerator')
            finally:
                if worker.is_alive():
                    worker.terminate()
                worker.join(timeout=2)
                worker.close()
                if handle:
                    api.CloseHandle(handle)


if __name__=='__main__':
    unittest.main()
