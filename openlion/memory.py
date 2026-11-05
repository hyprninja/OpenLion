"""Memory measurement without extra packages.

On Windows (the app's platform) this uses GlobalMemoryStatusEx and
GetProcessMemoryInfo through ctypes; elsewhere it falls back to /proc and
resource, which is enough for development.

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
