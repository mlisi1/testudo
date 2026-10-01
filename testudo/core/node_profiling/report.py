"""NodeStat: one process's resource reading plus its rolled-up CheckStatus."""
from __future__ import annotations

from dataclasses import dataclass

from diagnostic_msgs.msg import DiagnosticArray

from testudo.core.node_profiling.sampler import ProcessSample
from testudo.core.publisher import to_diagnostic_status
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity, ThresholdZone, colorize, evaluate_zone


@dataclass(frozen=True)
class NodeStat:
    """One tracked process's identity and current resource reading."""

    identity: str
    pid: int
    process_name: str
    resolved: bool
    cpu_percent: float
    cpu_affinity: tuple[int, ...] | None
    num_threads: int
    num_children: int
    memory_rss_mb: float
    gpu_memory_mb: float | None
    status: CheckStatus


#: `psutil.Process.status()` values worth flagging on their own, independent
#: of CPU/memory thresholds -- a "starving" node isn't always one burning
#: CPU; one stuck in uninterruptible I/O sleep is arguably worse (it's not
#: running at all) and a normal CPU%/memory reading wouldn't catch it.
_STATUS_SEVERITY: dict[str, int] = {
    "disk-sleep": Severity.WARN,
    "stopped": Severity.WARN,
    "zombie": Severity.ERROR,
    "dead": Severity.ERROR,
}
_STATUS_CODES: dict[str, str] = {
    "disk-sleep": "process is blocked in uninterruptible I/O sleep -- may be starving on I/O",
    "stopped": "process is stopped (e.g. by SIGSTOP), not running",
    "zombie": "process is a zombie (dead, not yet reaped by its parent)",
    "dead": "process is dead",
}


def format_duration(seconds: float) -> str:
    """"2h15m" / "3m40s" / "12s" -- compact human-readable uptime, not a raw seconds float."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes}m"
    if minutes:
        return f"{minutes}m{secs}s"
    return f"{secs}s"


def build_status(
    sample: ProcessSample,
    pid: int,
    process_name: str,
    cpu_zone: ThresholdZone | None,
    memory_zone: ThresholdZone | None,
    ctx_switch_zone: ThresholdZone | None,
    gpu_memory_mb: float | None = None,
) -> CheckStatus:
    """Roll `sample`'s readings up into one CheckStatus, including starvation checks.

    Two readings are placeholders on a process's first sample (see
    `ProcessSampler.sample`) -- `cpu_percent` (0.0) and
    `ctx_switches_involuntary_per_sec` (None) -- and don't contribute to
    severity until a real one exists; everything else (memory, process
    state, uptime, ...) is accurate immediately and evaluated right away,
    so a process that's already unhealthy on first sighting isn't
    reported as a false OK for one tick just because CPU% needs a second
    sample.

    "Starving" is checked two ways, both independent of CPU%/memory
    thresholds: a high *involuntary* context-switch rate (the OS scheduler
    repeatedly preempting the process against its will -- the direct
    signal for CPU contention; a high *voluntary* rate is normal, e.g.
    blocking on IPC, and isn't checked), and `sample.status` being
    "disk-sleep" (blocked on I/O) or worse (`_STATUS_SEVERITY`).
    """
    cpu_severity = Severity.OK if sample.warming_up else evaluate_zone(sample.cpu_percent, cpu_zone)
    memory_severity = evaluate_zone(sample.memory_rss_mb, memory_zone)
    status_severity = _STATUS_SEVERITY.get(sample.status, Severity.OK)
    ctx_switch_severity = Severity.OK

    codes: dict[str, str] = {}
    if sample.status in _STATUS_CODES:
        codes["NODE-002"] = colorize(_STATUS_CODES[sample.status], status_severity)
    if sample.ctx_switches_involuntary_per_sec is not None:
        ctx_switch_severity = evaluate_zone(sample.ctx_switches_involuntary_per_sec, ctx_switch_zone)
        if ctx_switch_severity >= Severity.WARN:
            codes["NODE-001"] = colorize(
                f"high involuntary context-switch rate ({sample.ctx_switches_involuntary_per_sec:.0f}/s) "
                "-- possible CPU starvation",
                ctx_switch_severity,
            )

    severity = max(cpu_severity, memory_severity, status_severity, ctx_switch_severity)

    values: dict[str, str] = {
        "pid": str(pid),
        "process_name": process_name,
        "cpu_percent": "warming up" if sample.warming_up else f"{sample.cpu_percent:.1f}",
        "memory_mb": f"{sample.memory_rss_mb:.1f}",
        "memory_percent": f"{sample.memory_percent:.2f}",
        "threads": str(sample.num_threads),
        "subprocesses": str(sample.num_children),
        "status": sample.status,
        "uptime": format_duration(sample.uptime_seconds),
    }
    if sample.cpu_affinity is not None:
        values["cpu_affinity"] = ",".join(str(core) for core in sample.cpu_affinity)
    if sample.nice is not None:
        values["nice"] = str(sample.nice)
    if sample.username is not None:
        values["username"] = sample.username
    if sample.num_fds is not None:
        values["open_fds"] = str(sample.num_fds)
    values["ctx_switches_voluntary"] = str(sample.ctx_switches_voluntary)
    values["ctx_switches_involuntary"] = str(sample.ctx_switches_involuntary)
    values["ctx_switches_involuntary_per_sec"] = (
        "warming up"
        if sample.ctx_switches_involuntary_per_sec is None
        else f"{sample.ctx_switches_involuntary_per_sec:.1f}"
    )
    if gpu_memory_mb is not None:
        values["gpu_memory_mb"] = f"{gpu_memory_mb:.1f}"

    message = f"cpu={sample.cpu_percent:.1f}% mem={sample.memory_rss_mb:.1f}MB"
    if ctx_switch_severity >= Severity.WARN:
        message += " (possible CPU starvation)"
    elif status_severity >= Severity.WARN:
        message += f" ({sample.status})"

    return CheckStatus(
        severity=severity,
        label="node-profile",
        message=message,
        values=values,
        # The Topic Panel's one plugin-defined extra column (see
        # topic_panel.py) -- CPU% is the single most useful at-a-glance
        # number; everything else above shows in the Detail Panel instead.
        topic_panel_column="CPU%",
        topic_panel_value="-" if sample.warming_up else colorize(f"{sample.cpu_percent:.1f}", cpu_severity),
        codes=codes,
    )


def node_stat_to_topic_report(stat: NodeStat) -> TopicReport:
    """Wrap `stat` in a synthetic `TopicReport` so it flows through the Plugin/Topic/Detail
    Panel machinery like any other check (see `tui/categorize.py`'s `NODES_CATEGORY`),
    rather than a separate screen. `topic` is the node identity (or `pid:<n>` if
    unresolved); `rate_hz=None` since there's no Hz concept for a process --
    the Topic Panel already renders a missing rate as "-", the same honest
    blank it uses when a real topic hasn't measured one yet.
    """
    return TopicReport(topic=stat.identity, msg_type="process", tier="node", status=stat.status, rate_hz=None)


def build_node_diagnostic_array(stamp, stats: list[NodeStat], overall: CheckStatus) -> DiagnosticArray:
    """One DiagnosticArray for the node-profiling topic: one status per process, plus "overall"."""
    array = DiagnosticArray()
    array.header.stamp = stamp
    array.status = [to_diagnostic_status(stat.identity, stat.status) for stat in stats]
    array.status.append(to_diagnostic_status("overall", overall))
    return array
