"""Unit tests for plain_values' markup/compact-variant stripping."""
from __future__ import annotations

from testudo.core.plain_values import plain_values, strip_markup


def test_strip_markup_removes_rich_tags() -> None:
    assert strip_markup("[bold red]0.031[/bold red] m") == "0.031 m"


def test_strip_markup_leaves_invalid_markup_unchanged() -> None:
    assert strip_markup("closing [/b] without opening") == "closing [/b] without opening"


def test_plain_values_drops_compact_variants_and_keeps_order() -> None:
    values = {"gauge": "[green]■■■[/green]", "gauge_compact": "[green]■[/green]", "frame_id": "odom"}
    assert list(plain_values(values).items()) == [("gauge", "■■■"), ("frame_id", "odom")]


def test_plain_values_single_line_drops_multiline_blocks() -> None:
    values = {"meters": "x ■■\ny ■■", "rate": "10"}
    assert plain_values(values, single_line=True) == {"rate": "10"}
    assert plain_values(values) == values


def test_plain_values_single_line_drops_more_values_but_publishing_keeps_them() -> None:
    values = {"rate": "10", "trend_more": "▁▂▃"}
    assert plain_values(values, single_line=True) == {"rate": "10"}
    assert plain_values(values) == values


def test_published_values_prefer_metrics_and_format_numbers_plainly() -> None:
    from testudo.core.plain_values import published_values
    from testudo.plugins.base import CheckStatus, Severity

    status = CheckStatus(
        Severity.OK,
        "l",
        "m",
        values={"success": "[green]50% ▮▮▮▯▯▯[/green]"},
        metrics={"success_rate": 0.5, "count": 3, "valid": True, "frame_id": "odom", "tiny": 1.234567e-5},
    )
    assert published_values(status) == {
        "success_rate": "0.5",
        "count": "3",
        "valid": "1",
        "frame_id": "odom",
        "tiny": "1.234567e-05",
    }


def test_published_values_fall_back_to_plain_single_line_values() -> None:
    from testudo.core.plain_values import published_values
    from testudo.plugins.base import CheckStatus, Severity

    status = CheckStatus(Severity.OK, "l", "m", values={"rate": "[b]10[/b]", "block": "a\nb", "x_more": "1"})
    assert published_values(status) == {"rate": "10"}
