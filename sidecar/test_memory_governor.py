"""Regression coverage for kill-at-cap and worker recovery."""

import unittest
import time

from memory_governor import MB, MemoryGovernor, physical_footprint, terminate_tree
import os
import tempfile
from pathlib import Path
from worker_client import WorkerDied, WorkerProcess


class MemoryGovernorTest(unittest.TestCase):
    def test_device_oom_discards_worker_and_allows_fresh_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "oom_worker.py"
            script.write_text(
                "import json, sys\n"
                "for line in sys.stdin:\n"
                "    req = json.loads(line)\n"
                "    if req['op'] == 'oom':\n"
                "        result = {'event': 'error', 'error_type': 'OutOfMemoryError', 'error': 'CUDA OOM'}\n"
                "    else:\n"
                "        result = {'event': 'result', 'data': 'recovered'}\n"
                "    print(json.dumps({'id': req['id'], **result}), flush=True)\n",
                encoding="utf-8",
            )
            worker = WorkerProcess(str(script), "device-oom-test")
            try:
                with self.assertRaisesRegex(WorkerDied, "CUDA OOM"):
                    worker.request("oom", timeout=5)
                first_generation = worker.generation
                self.assertFalse(worker.is_alive)
                self.assertEqual(worker.request("echo", timeout=5), "recovered")
                self.assertGreater(worker.generation, first_generation)
            finally:
                worker.stop(force=True)

    def test_crashed_owner_does_not_hang_on_descendant_stdout(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "crashing_worker.py"
            script.write_text(
                "import json, os, subprocess, sys\n"
                "request = json.loads(sys.stdin.readline())\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                "print(json.dumps({'id': request['id'], 'event': 'progress', 'data': {'child': child.pid}}), flush=True)\n"
                "os._exit(7)\n"
            )
            worker = WorkerProcess(str(script), "crash-test")
            progress = []
            started = time.monotonic()
            try:
                with self.assertRaises(WorkerDied):
                    worker.request("crash", on_progress=progress.append, timeout=5)
                self.assertLess(time.monotonic() - started, 3)
                self.assertFalse(worker.is_alive)
            finally:
                worker.shutdown()
                # Defensive cleanup if the assertion detects a regression.
                for state in progress:
                    try:
                        terminate_tree(state["child"])
                    except ProcessLookupError:
                        pass

    def test_balloon_is_killed_and_worker_restarts(self):
        worker = WorkerProcess("quality_worker.py", "balloon-test")
        governor = MemoryGovernor(
            {"quality": worker},
            cap_mb=int((physical_footprint(os.getpid()) or 0) / MB) + 96,
            minimum_cap_mb=32,
        )
        governor.start()
        try:
            with self.assertRaises(WorkerDied):
                worker.request(
                    "balloon",
                    {"megabytes": 160, "step_mb": 16, "hold_seconds": 5},
                    timeout=15,
                )
            deadline = time.monotonic() + 2
            while governor.health()["kills"] < 1 and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertGreaterEqual(governor.health()["kills"], 1)

            # A new request starts a fresh child and proves the serial request
            # path did not retain a poisoned lock or dead pipe.
            self.assertEqual(
                worker.request(
                    "balloon",
                    {"megabytes": 1, "hold_seconds": 0},
                    timeout=5,
                ),
                {"allocated_mb": 1},
            )
        finally:
            governor.stop()
            worker.shutdown()


if __name__ == "__main__":
    unittest.main()
