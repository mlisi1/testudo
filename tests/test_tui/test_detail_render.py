"""Detail Panel layout: synthetic TopicReports in, markup out -- no Textual app needed."""
from __future__ import annotations

from rich.text import Text

from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity
from testudo.tui.detail_render import humanize_pairs, render_detail, resolve_values


def _plain(markup: str) -> list[str]:
    return Text.from_markup(markup).plain.split("\n")


def _report(
    values: dict[str, str], severity: int = Severity.OK, tier: str = "full", publishers: tuple[str, ...] = (), **status_kwargs
) -> TopicReport:
    status = CheckStatus(severity, "l", "m", values, **status_kwargs)
    return TopicReport("/scan", "sensor_msgs/msg/LaserScan", tier, status, publishers=publishers)


def test_resolve_values_picks_the_compact_variant_only_when_narrow() -> None:
    values = {"gauge": "wide", "gauge_compact": "small", "other": "x"}
    assert resolve_values(values, is_narrow=False) == [("gauge", "wide"), ("other", "x")]
    assert resolve_values(values, is_narrow=True) == [("gauge", "small"), ("other", "x")]


def test_resolve_values_shows_more_variants_only_when_tall() -> None:
    values = {"rate": "9.9", "trend_more": "wide", "trend_more_compact": "small"}
    assert resolve_values(values, is_narrow=False) == [("rate", "9.9")]
    assert resolve_values(values, is_narrow=False, is_tall=True) == [("rate", "9.9"), ("trend", "wide")]
    assert resolve_values(values, is_narrow=True, is_tall=True) == [("rate", "9.9"), ("trend", "small")]


def test_render_detail_shows_more_values_only_in_a_tall_pane() -> None:
    report = _report({"rate": "9.9", "trend_more": "up"})
    assert not any("trend" in line for line in _plain(render_detail(report, width=100, height=7)))
    assert "trend  up" in _plain(render_detail(report, width=100, height=11))
    assert not any("trend" in line for line in _plain(render_detail(report, width=100)))


def test_humanize_moves_a_unit_suffix_from_the_key_onto_a_numeric_value() -> None:
    pairs = [("time_in_status_s", "12.4"), ("rate_hz", "9.98"), ("jump_speed_mps", "3"), ("frequency_per_min", "0.8")]
    assert humanize_pairs(pairs) == [
        ("time in status", "12.4 s"),
        ("rate", "9.98 Hz"),
        ("jump speed", "3 m/s"),
        ("frequency", "0.8 /min"),
    ]


def test_humanize_leaves_a_non_numeric_value_without_a_unit() -> None:
    assert humanize_pairs([("age_s", "n/a")]) == [("age", "n/a")]


def test_humanize_keeps_the_unit_in_the_label_when_two_keys_would_share_one() -> None:
    """`memory_mb`/`memory_percent` both reduce to "memory" -- as do a count and its own rate."""
    pairs = [("memory_mb", "640.0"), ("memory_percent", "2.31"), ("ctx_switches", "912"), ("ctx_switches_per_sec", "75.0")]
    assert humanize_pairs(pairs) == [
        ("memory (MB)", "640.0"),
        ("memory (%)", "2.31"),
        ("ctx switches", "912"),
        ("ctx switches (/s)", "75.0"),
    ]


def test_humanize_capitalizes_known_acronyms_and_never_empties_a_label() -> None:
    assert humanize_pairs([("pid", "1"), ("frame_id", "base"), ("open_fds", "5"), ("_s", "x")]) == [
        ("PID", "1"),
        ("frame ID", "base"),
        ("open FDs", "5"),
        (" s", "x"),
    ]


def test_header_shows_severity_badge_topic_and_type() -> None:
    header = _plain(render_detail(_report({}, Severity.ERROR), width=100))[0]
    assert header == "✗ ERROR  /scan  sensor_msgs/msg/LaserScan · full tier"


def test_header_drops_the_package_path_when_too_narrow_for_it() -> None:
    header = _plain(render_detail(_report({}, Severity.OK), width=30))[0]
    assert header == "✓ OK  /scan  LaserScan · full"


def test_excluded_topic_header_has_no_severity_badge() -> None:
    header = _plain(render_detail(_report({}, tier="excluded"), width=100))[0]
    assert header.startswith("/scan")


def test_few_values_stay_one_aligned_column() -> None:
    lines = _plain(render_detail(_report({"message_count": "412", "age_s": "17.20"}), width=100, height=10))
    assert lines[1:] == ["message count  412", "age            17.20 s"]


def test_more_values_than_rows_flow_into_extra_columns() -> None:
    values = {f"k{i}": str(i) for i in range(6)}
    lines = _plain(render_detail(_report(values), width=100, height=4))  # header + 3 grid rows
    assert lines[1:] == ["k0  0    k3  3", "k1  1    k4  4", "k2  2    k5  5"]


def test_grid_falls_back_to_fewer_columns_when_the_pane_is_too_narrow() -> None:
    values = {f"some_long_key_{i}": "value" for i in range(6)}
    lines = _plain(render_detail(_report(values), width=30, height=4))
    assert len(lines) == 1 + 6
    assert all(len(line) <= 30 for line in lines)


def test_multiline_block_is_shown_verbatim_in_its_original_position() -> None:
    values = {"before": "1", "block": "row a\nrow b", "after": "2"}
    lines = _plain(render_detail(_report(values), width=100, height=10))
    assert lines[1:] == ["before  1", "row a", "row b", "after  2"]


def test_long_value_gets_a_line_of_its_own() -> None:
    values = {"a": "1", "detail": "x" * 40, "b": "2"}
    lines = _plain(render_detail(_report(values), width=100, height=2))
    assert lines[1:] == ["a  1", f"detail  {'x' * 40}", "b  2"]


def test_empty_value_renders_as_a_dash_not_a_dangling_label() -> None:
    assert _plain(render_detail(_report({"detail": ""}), width=100))[1] == "detail  –"


def test_value_markup_does_not_break_column_alignment() -> None:
    values = {"a": "[bold red]1[/bold red]", "b": "22", "c": "3", "d": "4"}
    lines = _plain(render_detail(_report(values), width=100, height=3))
    assert lines[1:] == ["a  1     c  3", "b  22    d  4"]


def test_codes_come_last_after_a_blank_line() -> None:
    report = _report({"a": "1"}, Severity.WARN, codes={"SENS-001": "[yellow]stuck[/yellow]"})
    assert _plain(render_detail(report, width=100))[-2:] == ["", "[SENS-001] stuck"]


def test_blank_line_before_codes_is_dropped_when_it_would_push_a_code_out_of_view() -> None:
    report = _report({"a": "1"}, Severity.WARN, codes={"SENS-001": "stuck"})
    assert _plain(render_detail(report, width=100, height=3)) == ["▲ WARN  /scan  sensor_msgs/msg/LaserScan · full tier", "a  1", "[SENS-001] stuck"]
    assert _plain(render_detail(report, width=100, height=4))[2] == ""


def test_one_wide_value_moves_below_the_grid_rather_than_forcing_a_single_column() -> None:
    """Six short values need two columns to fit; the 20-wide one is all that stops a second column."""
    values = {"a": "1", "b": "2", "wide": "x" * 20, "c": "3", "d": "4", "e": "5", "f": "6"}
    lines = _plain(render_detail(_report(values), width=24, height=6))  # the header wraps to 2 rows at this width
    assert lines[1:] == ["a  1    d  4", "b  2    e  5", "c  3    f  6", f"wide  {'x' * 20}"]


def test_overflowing_layout_stays_a_plain_grid_when_moving_values_out_would_not_help() -> None:
    values = {f"key_{i}": "value" for i in range(8)}
    lines = _plain(render_detail(_report(values), width=14, height=3))
    assert lines[1:] == [f"key {i}  value" for i in range(8)]


def test_publishers_lead_a_presence_report_but_trail_a_full_one_only_when_tall() -> None:
    from dataclasses import replace

    from testudo.core.topic_report import presence_report

    presence = replace(presence_report("/cam/image_raw", "sensor_msgs/msg/Image"), publishers=("/cam (reliable, volatile)",))
    lines = _plain(render_detail(presence, width=100, height=6))
    assert lines[1] == "publisher  /cam (reliable, volatile)"
    assert "declare under topics: -> sensor_msgs/msg/Image" in lines[2]

    full = _report({"a": "1"}, publishers=("/x (reliable, volatile)", "/y (reliable, volatile)"))
    assert not any("publishers" in line for line in _plain(render_detail(full, width=100, height=6)))
    assert _plain(render_detail(full, width=100, height=12))[-1] == "publishers  /x (reliable, volatile), /y (reliable, volatile)"
