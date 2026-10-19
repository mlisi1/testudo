"""ProcessHistory: one process's recent CPU/memory readings, for trends and leak detection."""
from __future__ import annotations

from collections import deque

#: Readings kept per process: about two minutes at the default 2 s poll.
HISTORY_LENGTH = 60

#: The memory slope is only judged once the window spans at least this
#: long -- a node allocating its maps and buffers in its first seconds is
#: starting up, not leaking.
MIN_SLOPE_SPAN_SECONDS = 60.0


class ProcessHistory:
    """The last `HISTORY_LENGTH` (time, CPU%, RSS MB) readings of one process."""

    def __init__(self, length: int = HISTORY_LENGTH) -> None:
        self._samples: deque[tuple[float, float | None, float]] = deque(maxlen=length)

    def add(self, now: float, cpu_percent: float | None, memory_mb: float) -> None:
        """Record one reading; `cpu_percent=None` while the sampler is still warming up."""
        self._samples.append((now, cpu_percent, memory_mb))

    def cpu(self) -> tuple[float, ...]:
        """CPU% readings, oldest first, skipping warm-up placeholders."""
        return tuple(cpu for _, cpu, _ in self._samples if cpu is not None)

    def memory(self) -> tuple[float, ...]:
        """RSS readings in MB, oldest first."""
        return tuple(memory for _, _, memory in self._samples)

    def memory_slope_mb_per_min(self, min_span_seconds: float = MIN_SLOPE_SPAN_SECONDS) -> float | None:
        """Least-squares memory growth over the window in MB/min, or None until it spans `min_span_seconds`."""
        if len(self._samples) < 3:
            return None
        times = [t for t, _, _ in self._samples]
        if times[-1] - times[0] < min_span_seconds:
            return None
        memory = self.memory()
        mean_t = sum(times) / len(times)
        mean_m = sum(memory) / len(memory)
        spread = sum((t - mean_t) ** 2 for t in times)
        if spread <= 0:
            return None
        slope_per_second = sum((t - mean_t) * (m - mean_m) for t, m in zip(times, memory)) / spread
        return slope_per_second * 60.0
