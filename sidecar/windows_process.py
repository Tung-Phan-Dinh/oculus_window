"""Windows process accounting and owned, kill-on-close worker jobs.

Only imported on Windows. No shell utilities or third-party process package are
needed, and handles pin the process/job being operated on rather than a PID.
"""

import ctypes
from ctypes import wintypes
import threading
import time


_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi", use_last_error=True)
_SIZE_T = ctypes.c_size_t
_QUERY = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_owned_jobs = {}
_owned_jobs_lock = threading.Lock()


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", _SIZE_T),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
    ]


class _MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (name, _SIZE_T) for name in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
            "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
            "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
            "PrivateUsage",
        )
    ]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", _SIZE_T), ("MaximumWorkingSetSize", _SIZE_T),
        ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", _SIZE_T),
        ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", _SIZE_T), ("JobMemoryLimit", _SIZE_T),
        ("PeakProcessMemoryUsed", _SIZE_T), ("PeakJobMemoryUsed", _SIZE_T),
    ]


class _JobAccounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in (
        "TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime",
        "ThisPeriodTotalKernelTime",
    )] + [(name, wintypes.DWORD) for name in (
        "TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses",
    )]


def _bind(dll, name, arguments, result):
    function = getattr(dll, name)
    function.argtypes = arguments
    function.restype = result
    return function


_close = _bind(_kernel, "CloseHandle", [wintypes.HANDLE], wintypes.BOOL)
_snapshot = _bind(_kernel, "CreateToolhelp32Snapshot", [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE)
_first = _bind(_kernel, "Process32FirstW", [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)], wintypes.BOOL)
_next = _bind(_kernel, "Process32NextW", [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)], wintypes.BOOL)
_open = _bind(_kernel, "OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE)
_memory = _bind(_psapi, "GetProcessMemoryInfo", [wintypes.HANDLE, ctypes.POINTER(_MemoryCounters), wintypes.DWORD], wintypes.BOOL)
_wait = _bind(_kernel, "WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD)
_terminate = _bind(_kernel, "TerminateProcess", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL)
_create_job = _bind(_kernel, "CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE)
_set_job = _bind(_kernel, "SetInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL)
_assign_job = _bind(_kernel, "AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL)
_terminate_job = _bind(_kernel, "TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL)
_query_job = _bind(_kernel, "QueryInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL)


def process_parents() -> dict[int, int]:
    handle = _snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if handle == _INVALID_HANDLE:
        return {}
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        parents = {}
        more = _first(handle, ctypes.byref(entry))
        while more:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            more = _next(handle, ctypes.byref(entry))
        # ERROR_NO_MORE_FILES is the expected end, including an empty list.
        return parents if ctypes.get_last_error() == 18 else {}
    finally:
        _close(handle)


def _memory_counters(pid: int) -> _MemoryCounters | None:
    handle = _open(_QUERY, False, pid)
    if not handle:
        return None
    try:
        counters = _MemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if _memory(handle, ctypes.byref(counters), counters.cb):
            return counters
        return None
    finally:
        _close(handle)


def working_set_bytes(pid: int) -> int | None:
    """Resident host RAM, including mapped pages; the Windows RSS equivalent."""
    counters = _memory_counters(pid)
    return int(counters.WorkingSetSize) if counters is not None else None


def private_bytes(pid: int) -> int | None:
    """Private commit for diagnostics, including nonresident committed pages."""
    counters = _memory_counters(pid)
    return int(counters.PrivateUsage) if counters is not None else None


def is_running(pid: int) -> bool:
    handle = _open(_SYNCHRONIZE, False, pid)
    if not handle:
        # Access denied means unknown/alive, not permission to undercount it.
        return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER (gone)
    try:
        return _wait(handle, 0) != 0
    finally:
        _close(handle)


def terminate_pids(pids: list[int]) -> None:
    """Fallback for the one-shot parser; pin every handle before terminating."""
    handles = []
    try:
        for pid in pids:
            handle = _open(_TERMINATE | _SYNCHRONIZE, False, pid)
            if handle:
                handles.append(handle)
        for handle in handles:
            _terminate(handle, 1)
        for handle in handles:
            _wait(handle, 1000)
    finally:
        for handle in handles:
            _close(handle)


def terminate_owned_tree(pid: int) -> bool:
    """Use the parser's job too when the governor evicts one-shot work."""
    with _owned_jobs_lock:
        job = _owned_jobs.get(pid)
    if job is None:
        return False
    job.terminate()
    return True


class WorkerJob:
    """Closing this non-inherited handle kills only this job's descendants."""

    def __init__(self):
        self._lock = threading.Lock()
        self.pid = None
        self.handle = _create_job(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not _set_job(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process) -> None:
        if not _assign_job(self.handle, wintypes.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())
        self.pid = process.pid
        with _owned_jobs_lock:
            _owned_jobs[self.pid] = self

    def terminate(self) -> None:
        with self._lock:
            if self.handle and not _terminate_job(self.handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            # Windows termination is asynchronous. Wait for render descendants
            # to release files before the caller removes its staging directory.
            deadline = time.monotonic() + 5
            while self.handle and time.monotonic() < deadline:
                accounting = _JobAccounting()
                if not _query_job(self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if accounting.ActiveProcesses == 0:
                    break
                time.sleep(0.01)

    def close(self) -> None:
        with self._lock:
            if self.handle:
                _close(self.handle)
                self.handle = None
        with _owned_jobs_lock:
            if _owned_jobs.get(self.pid) is self:
                del _owned_jobs[self.pid]
