"""build_status / node_stat_to_topic_report: synthetic data only, no live process needed."""
from __future__ import annotations

from testudo.core.node_profiling.report import NodeStat, build_status, format_duration, node_stat_to_topic_report
from testudo.core.node_profiling.sampler import ProcessSample
from testudo.plugins.base import Severity, ThresholdZone

_CPU_ZONE = ThresholdZone(green=50.0, orange=90.0)
_MEM_ZONE = ThresholdZone(green=100.0, orange=500.0)
_CTX_ZONE = ThresholdZone(green=50.0, orange=200.0)


def _sample(**overrides) -> ProcessSample:
    defaults = dict(
        cpu_percent=10.0,
        cpu_affinity=(0, 1),
        num_threads=4,
        num_children=0,
        memory_rss_mb=50.0,
        memory_percent=1.5,
        status="running",
        uptime_seconds=125.0,
        ctx_switches_voluntary=1000,
        ctx_switches_involuntary=5,
        ctx_switches_involuntary_per_sec=1.0,
        nice=0,
        username="robot",
        num_fds=12,
        warming_up=False,
    )
    defaults.update(overrides)
    return ProcessSample(**defaults)


def _build(sample, cpu_zone=_CPU_ZONE, memory_zone=_MEM_ZONE, ctx_zone=_CTX_ZONE, **kwargs):
    return build_status(sample, 123, "controller_server", cpu_zone, memory_zone, ctx_zone, **kwargs)


def test_warming_up_sample_is_ok_but_still_shows_immediately_available_info() -> None:
    status = _build(_sample(warming_up=True, cpu_percent=999.0, status="disk-sleep"))
    # cpu_percent is a placeholder during warm-up, so it never contributes --
    # but process status (available immediately, no baseline needed) still does.
    assert status.severity == Severity.WARN
    assert status.values["cpu_percent"] == "warming up"
    assert status.values["status"] == "disk-sleep"


def test_healthy_sample_is_ok_with_topic_panel_cpu_column() -> None:
    status = _build(_sample(cpu_percent=10.0, memory_rss_mb=50.0))
    assert status.severity == Severity.OK
    assert status.topic_panel_column == "CPU%"
    assert "10.0" in status.topic_panel_value
    assert status.values["pid"] == "123"
    assert status.values["process_name"] == "controller_server"
    assert status.values["cpu_percent"] == "10.0"
    assert status.values["memory_mb"] == "50.0"
    assert status.values["memory_percent"] == "1.50"
    assert status.values["threads"] == "4"
    assert status.values["subprocesses"] == "0"
    assert status.values["cpu_affinity"] == "0,1"
    assert status.values["status"] == "running"
    assert status.values["uptime"] == "2m5s"
    assert status.values["nice"] == "0"
    assert status.values["username"] == "robot"
    assert status.values["open_fds"] == "12"
    assert status.values["ctx_switches_voluntary"] == "1000"
    assert status.values["ctx_switches_involuntary"] == "5"
    assert status.values["ctx_switches_involuntary_per_sec"] == "1.0"
    assert status.codes == {}


def test_high_cpu_drives_severity() -> None:
    status = _build(_sample(cpu_percent=95.0))
    assert status.severity == Severity.ERROR


def test_high_memory_drives_severity_even_with_low_cpu() -> None:
    status = _build(_sample(cpu_percent=1.0, memory_rss_mb=600.0))
    assert status.severity == Severity.ERROR


def test_high_involuntary_ctx_switch_rate_flags_starvation() -> None:
    status = _build(_sample(cpu_percent=5.0, memory_rss_mb=10.0, ctx_switches_involuntary_per_sec=500.0))
    assert status.severity == Severity.ERROR
    assert "NODE-001" in status.codes
    assert "starvation" in status.codes["NODE-001"]
    assert "starvation" in status.message


def test_moderate_involuntary_ctx_switch_rate_is_warn() -> None:
    status = _build(_sample(ctx_switches_involuntary_per_sec=100.0))
    assert status.severity == Severity.WARN
    assert "NODE-001" in status.codes


def test_low_involuntary_ctx_switch_rate_is_ok_and_uncoded() -> None:
    status = _build(_sample(ctx_switches_involuntary_per_sec=1.0))
    assert status.severity == Severity.OK
    assert "NODE-001" not in status.codes


def test_ctx_switch_rate_none_during_warmup_never_flags_starvation() -> None:
    status = _build(_sample(warming_up=True, ctx_switches_involuntary_per_sec=None))
    assert "NODE-001" not in status.codes


def test_disk_sleep_status_is_warn_with_code() -> None:
    status = _build(_sample(status="disk-sleep", cpu_percent=0.0))
    assert status.severity == Severity.WARN
    assert "NODE-002" in status.codes
    assert "I/O" in status.codes["NODE-002"]


def test_zombie_status_is_error_with_code() -> None:
    status = _build(_sample(status="zombie"))
    assert status.severity == Severity.ERROR
    assert "NODE-002" in status.codes
    assert "zombie" in status.codes["NODE-002"]


def test_normal_sleeping_status_is_ok_and_uncoded() -> None:
    status = _build(_sample(status="sleeping"))
    assert status.severity == Severity.OK
    assert "NODE-002" not in status.codes


def test_nice_username_num_fds_omitted_from_values_when_none() -> None:
    status = _build(_sample(nice=None, username=None, num_fds=None))
    assert "nice" not in status.values
    assert "username" not in status.values
    assert "open_fds" not in status.values


def test_gpu_memory_added_to_values_when_present() -> None:
    status = _build(_sample(), gpu_memory_mb=128.0)
    assert status.values["gpu_memory_mb"] == "128.0"


def test_gpu_memory_absent_when_none() -> None:
    status = _build(_sample(), gpu_memory_mb=None)
    assert "gpu_memory_mb" not in status.values


def test_no_zone_configured_is_always_ok() -> None:
    status = _build(_sample(cpu_percent=999.0, memory_rss_mb=999.0, ctx_switches_involuntary_per_sec=99999.0), None, None, None)
    assert status.severity == Severity.OK


def test_format_duration() -> None:
    assert format_duration(5) == "5s"
    assert format_duration(65) == "1m5s"
    assert format_duration(3725) == "1h2m"


def test_node_stat_to_topic_report_shape() -> None:
    status = _build(_sample())
    stat = NodeStat(
        identity="/controller_server",
        pid=123,
        process_name="controller_server",
        resolved=True,
        cpu_percent=10.0,
        cpu_affinity=(0, 1),
        num_threads=4,
        num_children=0,
        memory_rss_mb=50.0,
        gpu_memory_mb=None,
        status=status,
    )
    report = node_stat_to_topic_report(stat)
    assert report.topic == "/controller_server"
    assert report.msg_type == "process"
    assert report.tier == "node"
    assert report.rate_hz is None
    assert report.status is status
