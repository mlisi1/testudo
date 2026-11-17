"""Detail Panel values: variant picking and key humanizing, no app needed."""
from __future__ import annotations

from testudo.tui.detail_values import humanize_pairs, resolve_values



def test_resolve_values_picks_the_compact_variant_only_when_narrow() -> None:
    values = {"gauge": "wide", "gauge_compact": "small", "other": "x"}
    assert resolve_values(values, is_narrow=False) == [("gauge", "wide"), ("other", "x")]
    assert resolve_values(values, is_narrow=True) == [("gauge", "small"), ("other", "x")]


def test_resolve_values_shows_more_variants_only_when_tall() -> None:
    values = {"rate": "9.9", "trend_more": "wide", "trend_more_compact": "small"}
    assert resolve_values(values, is_narrow=False) == [("rate", "9.9")]
    assert resolve_values(values, is_narrow=False, is_tall=True) == [("rate", "9.9"), ("trend", "wide")]
    assert resolve_values(values, is_narrow=True, is_tall=True) == [("rate", "9.9"), ("trend", "small")]


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
