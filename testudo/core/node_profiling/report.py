"""NodeStat: one process's resource reading plus its rolled-up CheckStatus."""
from __future__ import annotations

from dataclasses import dataclass, replace

from diagnostic_msgs.msg import DiagnosticArray

from testudo.core.node_profiling.sampler import ProcessSample
from testudo.core.publisher import to_diagnostic_status
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity, ThresholdZone, colorize, evaluate_zone
from testudo.plugins.render import downsample, sparkline


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
    # Recent readings, oldest first, for the Detail Panel's trends (see `ProcessHistory`).
    cpu_history: tuple[float, ...] = ()
    memory_history: tuple[float, ...] = ()


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


#: The smallest scale the Detail Panel's trends draw on: one full core of
#: CPU, 10 MB of RSS -- so idle-process jitter doesn't fill the sparkline.
_CPU_TREND_MIN_RANGE = 100.0
_MEMORY_TREND_MIN_RANGE_MB = 10.0
#: Trend width in cells: the full history averaged down, so "CPU 123.4 %
#: <trend>" stays within the Detail Panel grid's widest value
#: (`detail_render._MAX_GRID_VALUE_WIDTH`) instead of taking a line of its own.
_TREND_WIDTH = 18

#: A process's first minutes are startup -- maps loaded, buffers and
#: caches filling to their steady size -- so its memory slope is only
#: judged (NODE-003) once it has been up this long.
LEAK_GRACE_SECONDS = 300.0


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
    num_children: int = 0,
    memory_slope_mb_per_min: float | None = None,
    memory_slope_zone: ThresholdZone | None = None,
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

    `memory_slope_mb_per_min` (None until `ProcessHistory` has a long
    enough window) is the leak check: steady RSS growth, `NODE-003` --
    shown from the start, but judged only after `LEAK_GRACE_SECONDS` of uptime.
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
                # Kept short: a message wider than the Detail Panel wraps,
                # taking a row from the readings it sits under.
                f"possible CPU starvation: preempted {sample.ctx_switches_involuntary_per_sec:.0f} times/s",
                ctx_switch_severity,
            )

    slope_severity = Severity.OK
    if memory_slope_mb_per_min is not None and sample.uptime_seconds >= LEAK_GRACE_SECONDS:
        slope_severity = evaluate_zone(memory_slope_mb_per_min, memory_slope_zone)
        if slope_severity >= Severity.WARN:
            codes["NODE-003"] = colorize(
                f"memory growing {memory_slope_mb_per_min:+.1f} MB/min -- possible leak", slope_severity
            )

    severity = max(cpu_severity, memory_severity, status_severity, ctx_switch_severity, slope_severity)

    values: dict[str, str] = {
        "pid": str(pid),
        "process_name": process_name,
        "cpu_percent": "warming up" if sample.warming_up else f"{sample.cpu_percent:.1f}",
        "memory_mb": f"{sample.memory_rss_mb:.1f}",
        "memory_percent": f"{sample.memory_percent:.2f}",
        "threads": str(sample.num_threads),
        "subprocesses": str(num_children),
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
    if memory_slope_mb_per_min is not None:
        values["memory_slope_mb_per_min"] = f"{memory_slope_mb_per_min:+.1f}"

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


def format_core_ranges(cores: str) -> str:
    """"0,1,2,3,8" -> "0-3,8": a comma-separated core list with consecutive runs collapsed.

    A process allowed on every core of a 16-core machine otherwise reads
    "0,1,2,...,15" -- wider than everything else in the Detail Panel put
    together. Returned unchanged if it isn't a plain list of integers.
    """
    try:
        numbers = sorted({int(core) for core in cores.split(",")})
    except ValueError:
        return cores
    ranges: list[str] = []
    start = previous = numbers[0]
    for number in numbers[1:] + [None]:
        if number is not None and number == previous + 1:
            previous = number
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        if number is not None:
            start = previous = number
    return ",".join(ranges)


def detail_panel_values(
    values: dict[str, str], cpu_history: tuple[float, ...] = (), memory_history: tuple[float, ...] = ()
) -> dict[str, str]:
    """`build_status`'s `values`, regrouped for reading in the Detail Panel rather than for plotting.

    The published `DiagnosticStatus` wants one plain number per key
    (`memory_mb`, `memory_percent`, `ctx_switches_voluntary`, ...) so a
    consumer can plot any of them; on screen that's 16 separate rows,
    several with labels long enough that the Detail Panel's grid can't
    fit two columns side by side in a tiled terminal -- and the whole
    thing scrolls. This pairs readings that belong together onto one row
    under a short label, in the order a person looks for them: how the
    process is doing, then CPU and memory (each with its recent trend,
    memory with its growth rate -- the leak signal), then scheduling.
    The process name and PID aren't repeated here --
    `node_stat_to_topic_report` puts them in the panel's header line.
    Only what `values` actually holds is shown (affinity, nice, user,
    open FDs and GPU are each absent on some platforms).
    """
    shown: dict[str, str] = {}

    process = [values[key] for key in ("status",) if key in values]
    if "uptime" in values:
        process.append(f"up {values['uptime']}")
    if "nice" in values:
        process.append(f"nice {values['nice']}")
    if "username" in values:
        process.append(f"user {values['username']}")
    if process:
        shown["process"] = " · ".join(process)

    if "cpu_percent" in values:
        cpu = values["cpu_percent"]
        unit = "" if cpu == "warming up" else " %"
        shown["cpu"] = f"{cpu}{unit}  {sparkline(downsample(cpu_history, _TREND_WIDTH), floor=0.0, min_range=_CPU_TREND_MIN_RANGE)}".rstrip()
    if "cpu_affinity" in values:
        shown["cpu_affinity"] = format_core_ranges(values["cpu_affinity"])
    # The starvation signal (see build_status): involuntary switches per second.
    if "ctx_switches_involuntary_per_sec" in values:
        shown["preempted_per_sec"] = values["ctx_switches_involuntary_per_sec"]

    if "memory_mb" in values:
        # First token stays the MB figure: the Topic Panel's Mem column parses it.
        memory = f"{values['memory_mb']} MB ({values.get('memory_percent', '?')} %)"
        trend = sparkline(downsample(memory_history, _TREND_WIDTH), min_range=_MEMORY_TREND_MIN_RANGE_MB)
        if trend:
            memory += f"  {trend}"
        if "memory_slope_mb_per_min" in values:
            memory += f"  {values['memory_slope_mb_per_min']} MB/min"
        shown["memory"] = memory
    if "gpu_memory_mb" in values:
        shown["gpu_memory_mb"] = values["gpu_memory_mb"]

    if "threads" in values:
        threads = f"{values['threads']} · subproc {values.get('subprocesses', '0')}"
        if "open_fds" in values:
            threads += f" · FDs {values['open_fds']}"
        shown["threads"] = threads
    if "ctx_switches_voluntary" in values:
        shown["ctx_switches"] = (
            f"{values['ctx_switches_voluntary']} vol / {values.get('ctx_switches_involuntary', '?')} invol"
        )
    return shown


def node_stat_to_topic_report(stat: NodeStat) -> TopicReport:
    """Wrap `stat` in a synthetic `TopicReport` so it flows through the Plugin/Topic/Detail
    Panel machinery like any other check (see `tui/categorize.py`'s `NODES_CATEGORY`),
    rather than a separate screen. `topic` is the node identity (or `testudo (self)`
    for Testudo's own unresolved process); `rate_hz=None` since there's no Hz concept for a process --
    the Topic Panel already renders a missing rate as "-", the same honest
    blank it uses when a real topic hasn't measured one yet.

    The report's status carries `detail_panel_values`' regrouped values,
    not `stat.status.values` as published -- this report only ever feeds
    the TUI, while `build_node_diagnostic_array` publishes `stat.status`
    itself, one plain number per key. `msg_type` (there is no message
    behind a process) carries the OS process name and PID, which is what
    the Detail Panel's header shows next to the node name -- one line
    for both identities instead of a row spent on each.
    """
    status = replace(
        stat.status, values=detail_panel_values(stat.status.values, stat.cpu_history, stat.memory_history)
    )
    msg_type = f"{stat.process_name} ({stat.pid})"
    return TopicReport(topic=stat.identity, msg_type=msg_type, tier="node", status=status, rate_hz=None)


def build_node_diagnostic_array(stamp, stats: list[NodeStat], overall: CheckStatus) -> DiagnosticArray:
    """One DiagnosticArray for the node-profiling topic: one status per process, plus "overall"."""
    array = DiagnosticArray()
    array.header.stamp = stamp
    array.status = [to_diagnostic_status(stat.identity, stat.status) for stat in stats]
    array.status.append(to_diagnostic_status("overall", overall))
    return array
