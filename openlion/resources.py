"""How hard the build may push this PC: decided once from free RAM and CPU count.

Every OSM read and every heavy stage runs in a process of its own, so the build's
memory is the biggest single step (low plan) or the steps that overlap (high plan).

Low plan (small PCs, 8 GB): osmium keeps node locations in a file on disk, reads and
stages run one after another, the background map is built strip by strip in one
process. Peak: about 3.6 GB (roads stage) for Australia + New Zealand.

High plan: reads run side by side with node locations in memory; the background map
and the POIs run alongside the roads stage; the background map's strips go to as many
worker processes as memory and cores allow.

Override with the environment variable OPENLION_MEMORY=low|high (and
OPENLION_WORKERS=n for the worker counts), or make_card / pipeline --memory low|high.
"""
import os

from . import memory

GB = memory.GB

# Measured on Australia + New Zealand (report 20), rounded up.
LOW_PEAK = 4.0 * GB           # biggest stage on the low plan (roads stage 3.6 GB)
STAGES_HIGH = 7.5 * GB        # roads + background-map parent + POI stages side by side
READ_WORKER = 6.0 * GB        # one OSM read with its node locations in memory (Australia: 5.1-5.9)
FEA_WORKER = 0.6 * GB         # one background-map strip worker
RESERVE = 1.5 * GB            # left for Windows and the app


class Plan:
    def __init__(self, mode, read_workers, fea_workers, disk_index, available, cpus):
        self.mode = mode                  # "low" or "high"
        self.read_workers = read_workers  # OSM reads at once
        self.fea_workers = fea_workers    # background-map processes (1 = in the stage process)
        self.disk_index = disk_index      # node locations in a file instead of RAM
        self.available = available
        self.cpus = cpus

    def node_index(self, work_dir, name="nodes"):
        """osmium location storage for one read."""
        if self.disk_index:
            return f"sparse_file_array,{os.path.join(os.path.abspath(work_dir), name + '.idx')}"
        return "flex_mem"

    def describe(self):
        return (f"{self.mode} memory plan: {memory.gb(self.available)} free, {self.cpus} CPUs -> "
                f"{self.read_workers} OSM read(s) at once, {self.fea_workers} background-map worker(s), "
                f"node locations {'on disk' if self.disk_index else 'in memory'}")


def make_plan(mode=None, available=None, cpus=None):
    mode = (mode or os.environ.get("OPENLION_MEMORY") or "auto").lower()
    available = memory.available() if available is None else available
    cpus = cpus or os.cpu_count() or 1
    usable = available - RESERVE
    if mode == "auto":
        mode = "high" if usable >= STAGES_HIGH + 2 * FEA_WORKER and usable >= 2 * READ_WORKER and cpus >= 4 \
            else "low"
    if mode == "low":
        return Plan("low", 1, 1, True, available, cpus)
    reads = int(max(1, min(cpus - 1, usable // READ_WORKER)))
    fea = int(max(2, min(cpus - 2, (usable - STAGES_HIGH) // FEA_WORKER)))
    forced = os.environ.get("OPENLION_WORKERS")
    if forced:
        fea = reads = max(1, int(forced))
    return Plan("high", reads, fea, False, available, cpus)
