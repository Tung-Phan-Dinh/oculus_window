"""Real Windows lifecycle tests; no models or network needed."""

import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from memory_governor import physical_footprint, sample_tree, terminate_tree
from process_runtime import spawn_python
from worker_client import WorkerProcess


@unittest.skipUnless(os.name == "nt", "Windows process APIs")
class WindowsRuntimeTest(unittest.TestCase):
    def test_governor_counts_resident_pages_not_untouched_commit(self):
        import ctypes
        from ctypes import wintypes
        from windows_process import private_bytes, working_set_bytes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        allocate = kernel.VirtualAlloc
        allocate.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
        allocate.restype = ctypes.c_void_p
        release = kernel.VirtualFree
        release.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
        release.restype = wintypes.BOOL
        pid = os.getpid()
        before_commit = private_bytes(pid)
        before_resident = working_set_bytes(pid)
        allocation = allocate(None, 256 * 1024**2, 0x3000, 0x04)
        self.assertTrue(allocation)
        try:
            # Committed zero-fill pages consume commit charge immediately, but
            # consume RAM only when touched. A RAM governor must distinguish it.
            self.assertGreater(private_bytes(pid) - before_commit, 240 * 1024**2)
            self.assertLess(physical_footprint(pid) - before_resident, 32 * 1024**2)
            ctypes.memset(allocation, 1, 64 * 1024**2)
            self.assertGreater(physical_footprint(pid) - before_resident, 48 * 1024**2)
        finally:
            self.assertTrue(release(allocation, 0, 0x8000))

    def test_snapshot_includes_child_and_memory(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "child.py"
            script.write_text(
                "import json, os, sys, time\n"
                "print(json.dumps({'pid': os.getpid(), 'prefix': sys.prefix}), flush=True)\n"
                "time.sleep(30)\n", encoding="utf-8",
            )
            child, job = spawn_python(str(script), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                actual = json.loads(child.stdout.readline())
                self.assertEqual(actual["pid"], child.pid)
                self.assertEqual(actual["prefix"], sys.prefix)
                snapshot = sample_tree(os.getpid())
                self.assertTrue(snapshot.complete)
                self.assertIn(child.pid, snapshot.by_pid)
                self.assertGreater(physical_footprint(child.pid), 0)
                self.assertEqual(snapshot.parents[child.pid], os.getpid())
            finally:
                job.close()
                child.communicate(timeout=5)

    def test_job_close_kills_descendant_after_owner_exits(self):
        from windows_process import is_running

        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "owner.py"
            script.write_text(
                "import os, subprocess, sys\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                "print(child.pid, flush=True)\n"
                "os._exit(7)\n", encoding="utf-8",
            )
            owner, job = spawn_python(str(script), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            child_pid = None
            try:
                child_pid = int(owner.stdout.readline())
                owner.wait(timeout=5)
                self.assertTrue(is_running(child_pid))
                job.close()
                deadline = time.monotonic() + 3
                while is_running(child_pid) and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertFalse(is_running(child_pid))
            finally:
                job.close()
                owner.communicate(timeout=5)

    def test_governor_tree_termination_uses_owned_job(self):
        from windows_process import is_running
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "parser.py"
            script.write_text(
                "import subprocess, sys, time\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                "print(child.pid, flush=True)\n"
                "time.sleep(30)\n", encoding="utf-8",
            )
            owner, job = spawn_python(str(script), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                child_pid = int(owner.stdout.readline())
                # Even a child missing from a racy process snapshot remains in
                # the job and must be killed with its owner.
                with patch("memory_governor.descendant_pids", return_value=[]):
                    terminate_tree(owner.pid)
                owner.communicate(timeout=5)
                self.assertFalse(is_running(child_pid))
            finally:
                job.close()
                owner.communicate(timeout=5)

    def test_worker_jobs_nest_under_application_job(self):
        from windows_process import is_running

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            leaf = root / "leaf.py"
            leaf.write_text(
                "import json, sys\n"
                "for line in sys.stdin:\n"
                "    req = json.loads(line)\n"
                "    print(json.dumps({'id': req['id'], 'event': 'result', 'data': 'ok'}), flush=True)\n",
                encoding="utf-8",
            )
            script = root / "sidecar.py"
            script.write_text(
                "import json, time\n"
                "from worker_client import WorkerProcess\n"
                f"worker = WorkerProcess({str(leaf)!r}, 'nested-test')\n"
                "result = worker.request('echo', timeout=5)\n"
                "print(json.dumps({'pid': worker.pid, 'result': result}), flush=True)\n"
                "time.sleep(30)\n", encoding="utf-8",
            )
            owner, job = spawn_python(str(script), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                response = json.loads(owner.stdout.readline())
                self.assertEqual(response["result"], "ok")
                job.terminate()
                owner.communicate(timeout=5)
                self.assertFalse(is_running(response["pid"]))
            finally:
                job.close()
                owner.communicate(timeout=5)

    def test_unicode_worker_protocol_ignores_legacy_parent_encoding(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory(prefix="oculus 测试 ") as temporary:
            script = Path(temporary) / "解析.py"
            script.write_text(
                "import json, sys\n"
                "for line in sys.stdin:\n"
                "    request = json.loads(line)\n"
                "    print(json.dumps({'id': request['id'], 'event': 'result', "
                "'data': request.get('text')}, ensure_ascii=False), flush=True)\n",
                encoding="utf-8",
            )
            worker = WorkerProcess(str(script), "unicode-test")
            try:
                with patch.dict(os.environ, {"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}):
                    value = "C:\\学生 files\\积分 αβ 🪐.pdf"
                    self.assertEqual(worker.request("echo", {"text": value}, timeout=5), value)
            finally:
                worker.stop(force=True)


if __name__ == "__main__":
    unittest.main()
