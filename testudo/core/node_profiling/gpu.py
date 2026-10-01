"""Best-effort, NVIDIA-only, memory-only per-process GPU stats.

Lazily imports `pynvml` -- it's not declared as a dependency anywhere
(most robots have no GPU, and even NVIDIA ones may not have `pynvml`
installed), so an absent/unimportable/no-device environment degrades to
"no GPU data" rather than failing node profiling entirely, matching the
project's existing stance on optional message-interface packages.

Deliberately memory-only: there is no reliable, cheaply-pollable
per-process GPU *utilization* number on any vendor (NVML doesn't expose
per-process SM occupancy outside of dedicated profiling tools) -- reporting
one would be either misleading or require overhead not worth paying for a
periodic lightweight profiler.
"""
from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

_available: bool | None = None
_warned_unavailable = False
_pynvml = None


def gpu_available() -> bool:
    """Whether NVML GPU stats can be queried this run. Cached after the first call."""
    global _available, _pynvml, _warned_unavailable
    if _available is not None:
        return _available
    try:
        import pynvml

        pynvml.nvmlInit()
        _available = pynvml.nvmlDeviceGetCount() > 0
        _pynvml = pynvml
    except Exception:
        _available = False
    if not _available and not _warned_unavailable:
        _logger.info("GPU profiling unavailable (pynvml not installed, or no NVIDIA GPU detected) -- skipping")
        _warned_unavailable = True
    return _available


def per_process_gpu_memory_mb(pid: int) -> float | None:
    """Total GPU memory `pid` currently holds across every visible device, or None if unavailable.

    Catches `NVMLError` per call (not just at import/init time) so a
    transient driver/permission hiccup on one query degrades that single
    reading to None rather than disabling GPU stats for the rest of the run.
    """
    if not gpu_available():
        return None
    pynvml = _pynvml
    total_bytes = 0
    found = False
    try:
        for index in range(pynvml.nvmlDeviceGetCount()):
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            for getter in (pynvml.nvmlDeviceGetComputeRunningProcesses, pynvml.nvmlDeviceGetGraphicsRunningProcesses):
                try:
                    for proc_info in getter(handle):
                        if proc_info.pid == pid:
                            total_bytes += proc_info.usedGpuMemory or 0
                            found = True
                except pynvml.NVMLError:
                    continue
    except pynvml.NVMLError:
        return None
    return (total_bytes / (1024 * 1024)) if found else None


def reset_for_testing() -> None:
    """Clear the cached availability check. Test-only -- production code never needs this."""
    global _available, _pynvml, _warned_unavailable
    _available = None
    _pynvml = None
    _warned_unavailable = False
