"""Consistent UTF-8 Python pipes, with owned worker trees on Windows."""

import os
from pathlib import Path
import subprocess
import sys


def spawn_python(script: str, *arguments: str, **kwargs):
    """Return (Popen, Windows job or None); the caller must close the job.

    The Windows bootstrap waits before importing worker code, so even a worker
    that immediately spawns children cannot escape its job during startup.
    """
    environment = {**os.environ, **kwargs.pop("env", {}),
                   "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1",
                   "PYTHONIOENCODING": "utf-8"}
    command = [sys.executable, script, *arguments]
    job = None
    if os.name == "nt":
        from windows_process import WorkerJob

        job = WorkerJob()
        # Windows venv python.exe redirects to another process. That child can
        # start before the redirector is assigned to the job. Launch the actual
        # interpreter and tell CPython which venv launcher it represents, so
        # the gated process itself is the one we own, with the same site-packages.
        command[0] = getattr(sys, "_base_executable", sys.executable)
        environment["__PYVENV_LAUNCHER__"] = sys.executable
        command.insert(1, str(Path(__file__).with_name("process_bootstrap.py")))
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
    process = None
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, text=True, encoding="utf-8",
            env=environment, **kwargs,
        )
        if job is not None:
            job.assign(process)
            process.stdin.write("oculus-worker-ready\n")
            process.stdin.flush()
        return process, job
    except BaseException:
        if process is not None:
            process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        if job is not None:
            job.close()
        raise
