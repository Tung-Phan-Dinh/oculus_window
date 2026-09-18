"""Do not execute a Windows worker until its parent assigns the worker job."""

import runpy
import sys


if __name__ == "__main__":
    if sys.stdin.readline() != "oculus-worker-ready\n":
        raise SystemExit("worker ownership handshake failed")
    sys.argv = sys.argv[1:]
    runpy.run_path(sys.argv[0], run_name="__main__")
