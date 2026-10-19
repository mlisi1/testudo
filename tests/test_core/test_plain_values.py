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
