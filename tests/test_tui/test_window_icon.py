"""window_icon: the decisions and the icon data, without an X server."""
from __future__ import annotations

import importlib.resources

import pytest

from testudo.tui.window_icon import icon_sizes, set_window_icon, should_try, unpack_icon, window_id_from_env


def test_tries_on_x11_and_on_wayland_only_with_a_window_id() -> None:
    assert should_try({"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"})
    assert not should_try({})
    assert not should_try({"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"})
    assert should_try({"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0", "WINDOWID": "77"})


def test_window_id_parses_decimal_and_hex_and_ignores_junk() -> None:
    assert window_id_from_env({"WINDOWID": "96468994"}) == 96468994
    assert window_id_from_env({"WINDOWID": "0x5c0000c"}) == 0x5C0000C
    assert window_id_from_env({"WINDOWID": "nope"}) is None
    assert window_id_from_env({}) is None


def test_packaged_icon_is_a_well_formed_net_wm_icon_with_the_usual_sizes() -> None:
    data = importlib.resources.files("testudo.tui.assets").joinpath("window_icon.bin").read_bytes()
    cardinals = unpack_icon(data)
    sizes = icon_sizes(cardinals)
    assert (16, 16) in sizes and (32, 32) in sizes and (48, 48) in sizes
    assert all(value < 1 << 32 for value in cardinals)
    # Transparent corners, an opaque middle: a shield, not a filled square.
    width, height = sizes[0]
    pixels = cardinals[2 : 2 + width * height]
    assert pixels[0] >> 24 == 0
    assert pixels[(height // 2) * width + width // 2] >> 24 == 0xFF


def test_icon_sizes_rejects_truncated_data() -> None:
    with pytest.raises(ValueError):
        icon_sizes([2, 2, 0, 0, 0])


def test_nothing_is_attempted_without_a_display() -> None:
    titles: list[str] = []
    assert set_window_icon(titles.append, environ={}) is None
    assert titles == []
