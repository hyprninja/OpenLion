"""Memory measurement without extra packages.

On Windows this uses GlobalMemoryStatusEx and GetProcessMemoryInfo through
ctypes; on macOS host_statistics64 and proc_pid_rusage, also through ctypes;
elsewhere it falls back to /proc and resource, which is enough for development.

  available()      bytes of RAM other programs aren't using right now
  total()          installed RAM
  process()        {"private": committed bytes, "ws": working set, "peak_ws": ...}
  Sampler          background thread that keeps the peak private bytes per stage
"""
import os
import sys
import threading
import time

GB = 1 << 30

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _MEMSTAT(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    class _PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t)]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)
    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]

    def _memstat():
        m = _MEMSTAT()
        m.dwLength = ctypes.sizeof(m)
        _k32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m

    def available():
        return _memstat().ullAvailPhys

    def total():
        return _memstat().ullTotalPhys

    def process():
        c = _PMC()
        c.cb = ctypes.sizeof(c)
        _psapi.GetProcessMemoryInfo(_k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
        return {"private": c.PrivateUsage, "ws": c.WorkingSetSize, "peak_ws": c.PeakWorkingSetSize,
                "peak_private": c.PeakPagefileUsage}
elif sys.platform == "darwin":
    import ctypes
    import resource

    _libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)

    class _VMStat64(ctypes.Structure):  # vm_statistics64 (mach/vm_statistics.h)
        _fields_ = [("free_count", ctypes.c_uint32), ("active_count", ctypes.c_uint32),
                    ("inactive_count", ctypes.c_uint32), ("wire_count", ctypes.c_uint32),
                    ("zero_fill_count", ctypes.c_uint64), ("reactivations", ctypes.c_uint64),
                    ("pageins", ctypes.c_uint64), ("pageouts", ctypes.c_uint64),
                    ("faults", ctypes.c_uint64), ("cow_faults", ctypes.c_uint64),
                    ("lookups", ctypes.c_uint64), ("hits", ctypes.c_uint64),
                    ("purges", ctypes.c_uint64), ("purgeable_count", ctypes.c_uint32),
                    ("speculative_count", ctypes.c_uint32), ("decompressions", ctypes.c_uint64),
                    ("compressions", ctypes.c_uint64), ("swapins", ctypes.c_uint64),
                    ("swapouts", ctypes.c_uint64), ("compressor_page_count", ctypes.c_uint32),
                    ("throttled_count", ctypes.c_uint32), ("external_page_count", ctypes.c_uint32),
                    ("internal_page_count", ctypes.c_uint32),
                    ("total_uncompressed_pages_in_compressor", ctypes.c_uint64)]

    class _RUsageV0(ctypes.Structure):  # rusage_info_v0 (sys/resource.h)
        _fields_ = [("uuid", ctypes.c_uint8 * 16), ("user_time", ctypes.c_uint64),
                    ("system_time", ctypes.c_uint64), ("pkg_idle_wkups", ctypes.c_uint64),
                    ("interrupt_wkups", ctypes.c_uint64), ("pageins", ctypes.c_uint64),
                    ("wired_size", ctypes.c_uint64), ("resident_size", ctypes.c_uint64),
                    ("phys_footprint", ctypes.c_uint64), ("proc_start_abstime", ctypes.c_uint64),
                    ("proc_exit_abstime", ctypes.c_uint64)]

    _libc.mach_host_self.restype = ctypes.c_uint32
    _libc.host_statistics64.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_void_p,
                                        ctypes.POINTER(ctypes.c_uint32)]
    _libc.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    _HOST_VM_INFO64 = 4
    _host = _libc.mach_host_self()
    _page = os.sysconf("SC_PAGE_SIZE")  # the kernel's page size: 16 KB on Apple silicon

    def available():
        # Activity Monitor's view: "Memory Used" is app memory (anonymous pages that can't just be
        # dropped) + wired + compressed; everything else, including the file cache macOS gives back on
        # demand, is available.
        s = _VMStat64()
        n = ctypes.c_uint32(ctypes.sizeof(s) // 4)
        if _libc.host_statistics64(_host, _HOST_VM_INFO64, ctypes.byref(s), ctypes.byref(n)) != 0:
            return 0
        used = (max(0, s.internal_page_count - s.purgeable_count) + s.wire_count + s.compressor_page_count) * _page
        return max(0, total() - used)

    def total():
        return os.sysconf("SC_PHYS_PAGES") * _page

    def process():
        # phys_footprint is what Activity Monitor shows as "Memory": the closest match to
        # Windows' private bytes. ru_maxrss is in bytes on macOS.
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        u = _RUsageV0()
        now = u.phys_footprint if _libc.proc_pid_rusage(os.getpid(), 0, ctypes.byref(u)) == 0 else 0
        return {"private": now, "ws": u.resident_size, "peak_ws": peak, "peak_private": max(peak, now)}
else:
    def _meminfo(key):
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith(key + ":"):
                        return int(line.split()[1]) * 1024
        except OSError:
            pass
        return 0

    def available():
        return _meminfo("MemAvailable")

    def total():
        return _meminfo("MemTotal")

    def process():
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
        rss = 0
        try:
            with open("/proc/self/statm") as f:
                rss = int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        except OSError:
            pass
        return {"private": rss, "ws": rss, "peak_ws": peak, "peak_private": peak}


def gb(n):
    return f"{n / GB:.1f} GB"


class Sampler:
    """Samples this process's private bytes every `interval` seconds and keeps the
    peak per stage. stage(name) starts a new stage; peaks() returns {name: bytes}."""

    def __init__(self, interval=0.25):
        self.interval = interval
        self.name = "start"
        self.peak = {}
        self.min_avail = None
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _sample(self):
        p = process()["private"]
        if p > self.peak.get(self.name, 0):
            self.peak[self.name] = p
        a = available()
        if self.min_avail is None or a < self.min_avail:
            self.min_avail = a

    def _run(self):
        while not self._stop.wait(self.interval):
            self._sample()

    def stage(self, name):
        self._sample()          # close the previous stage with a final reading
        self.name = name
        self._sample()

    def stage_peak(self, name=None):
        return self.peak.get(name or self.name, 0)

    def stop(self):
        self._stop.set()
        self._t.join(timeout=2)
        self._sample()

    def peaks(self):
        return dict(self.peak)
