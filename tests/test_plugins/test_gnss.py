"""Unit tests for GnssPlugin -- synthetic NavSatFix, no live ROS graph."""
from __future__ import annotations

from sensor_msgs.msg import NavSatFix, NavSatStatus

from rich.text import Text

from testudo.plugins.base import Severity
from testudo.plugins.builtin.gnss import GnssPlugin


def _plain(markup: str) -> str:
    return Text.from_markup(markup).plain

_METERS_PER_DEGREE_LAT = 111_000.0  # ~ at the equator, close enough for test fixtures


def _fix(
    lat: float = 45.0,
    lon: float = 9.0,
    alt: float = 10.0,
    status: int = NavSatStatus.STATUS_FIX,
    covariance: tuple[float, ...] | None = None,
    covariance_type: int = NavSatFix.COVARIANCE_TYPE_DIAGONAL_KNOWN,
    frame_id: str = "gps",
    stamp_sec: int = 0,
    stamp_nanosec: int = 0,
) -> NavSatFix:
    msg = NavSatFix()
    msg.header.frame_id = frame_id
    msg.header.stamp.sec = stamp_sec
    msg.header.stamp.nanosec = stamp_nanosec
    msg.status.status = status
    msg.latitude = lat
    msg.longitude = lon
    msg.altitude = alt
    msg.position_covariance_type = covariance_type
    if covariance is not None:
        msg.position_covariance = list(covariance)
    return msg


def test_no_messages_yet_is_ok() -> None:
    plugin = GnssPlugin()
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert "no messages received" in status.message


def test_nominal_fix_is_ok() -> None:
    plugin = GnssPlugin()
    plugin.on_message("/gps/fix", _fix(covariance=(0.0001, 0, 0, 0, 0.0001, 0, 0, 0, 0.0001)))
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert _plain(status.values["fix"]).startswith("● FIX   σ horiz ±0.01m ▮▮▮▮▮▮   σ vert ±0.01m ▮▮▮▮▮▮")
    assert _plain(status.topic_panel_value) == "FIX ±0.01"


def test_no_fix_is_error() -> None:
    plugin = GnssPlugin()
    plugin.on_message("/gps/fix", _fix(status=NavSatStatus.STATUS_NO_FIX))
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "no fix" in status.message
    assert "GNSS-001" in status.codes


def test_sbas_fix_is_ok() -> None:
    plugin = GnssPlugin()
    plugin.on_message(
        "/gps/fix", _fix(status=NavSatStatus.STATUS_SBAS_FIX, covariance=(0.0001, 0, 0, 0, 0.0001, 0, 0, 0, 0.0001))
    )
    assert plugin.get_status().severity == Severity.OK


def test_unknown_covariance_type_does_not_penalize_zero_covariance() -> None:
    # Real hardware commonly never fills position_covariance; an all-zero
    # array with COVARIANCE_TYPE_UNKNOWN must not read as "perfect accuracy".
    plugin = GnssPlugin()
    plugin.on_message(
        "/gps/fix", _fix(covariance=(0, 0, 0, 0, 0, 0, 0, 0, 0), covariance_type=NavSatFix.COVARIANCE_TYPE_UNKNOWN)
    )
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert "σ horiz n/a (unknown)" in _plain(status.values["fix"])
    assert "▮" not in status.values["fix"]  # no reassuring green meter for an unreported covariance
    assert _plain(status.topic_panel_value) == "FIX"


def test_known_covariance_above_threshold_is_flagged() -> None:
    plugin = GnssPlugin()
    # 0.1 m^2 (~32cm std) is well past the RTK-tuned orange bound (0.01).
    plugin.on_message("/gps/fix", _fix(covariance=(0.1, 0, 0, 0, 0.1, 0, 0, 0, 0.1)))
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "position covariance out of bounds" in status.message
    assert "GNSS-002" in status.codes


def test_covariance_flags_the_specific_bad_axis() -> None:
    # Only z (altitude) is bad -- x/y should not be implicated in the message,
    # and shouldn't be hidden behind a summed trace either.
    plugin = GnssPlugin()
    plugin.on_message("/gps/fix", _fix(covariance=(0.0001, 0, 0, 0, 0.0001, 0, 0, 0, 0.5)))
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "z=" in status.message
    assert "x=" not in status.message
    assert "y=" not in status.message
    # The vertical meter judges z; the horizontal one stays green.
    assert "σ vert ±0.71m [bold red]▮" in status.values["fix"]
    assert "σ horiz ±0.01m [green]▮▮▮▮▮▮" in status.values["fix"]


def test_nan_latitude_counts_as_invalid() -> None:
    plugin = GnssPlugin()
    plugin.on_message("/gps/fix", _fix(lat=float("nan"), covariance=(0.0001, 0, 0, 0, 0.0001, 0, 0, 0, 0.0001)))
    status = plugin.get_status()
    assert status.severity != Severity.OK
    assert "invalid ratio" in status.message


def test_stuck_fix_escalates_after_repeats() -> None:
    plugin = GnssPlugin()
    fix = _fix(covariance=(0.0001, 0, 0, 0, 0.0001, 0, 0, 0, 0.0001))
    for _ in range(4):
        plugin.on_message("/gps/fix", fix)
    status = plugin.get_status()
    assert status.severity == Severity.WARN
    assert "stuck for" in status.message
    assert status.values["stuck"] == "3"
    assert "GNSS-005" in status.codes


def test_frame_id_change_is_flagged() -> None:
    plugin = GnssPlugin()
    plugin.on_message("/gps/fix", _fix(frame_id="gps_a"))
    plugin.on_message("/gps/fix", _fix(frame_id="gps_b", stamp_sec=1))
    status = plugin.get_status()
    assert status.severity == Severity.WARN
    assert "frame_id changed" in status.message


def test_implausible_jump_between_real_fixes_is_flagged() -> None:
    plugin = GnssPlugin()
    plugin.on_message("/gps/fix", _fix(lat=45.0, lon=9.0, stamp_sec=0))
    # ~1.11 km north in 1s => ~1110 m/s, well past the 30 m/s orange bound.
    jumped_lat = 45.0 + (1000.0 / _METERS_PER_DEGREE_LAT)
    plugin.on_message("/gps/fix", _fix(lat=jumped_lat, lon=9.0, stamp_sec=1))
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "implausible jump" in status.message


def test_jump_is_not_evaluated_across_a_dropout() -> None:
    plugin = GnssPlugin()
    plugin.on_message("/gps/fix", _fix(lat=45.0, lon=9.0, stamp_sec=0))
    plugin.on_message("/gps/fix", _fix(status=NavSatStatus.STATUS_NO_FIX, stamp_sec=1))
    jumped_lat = 45.0 + (1000.0 / _METERS_PER_DEGREE_LAT)
    plugin.on_message("/gps/fix", _fix(lat=jumped_lat, lon=9.0, stamp_sec=2))
    status = plugin.get_status()
    # The fix immediately after a dropout has no valid "previous real fix"
    # to compare against (the dropout message cleared it), so no jump_speed
    # should have been computed from the pre-dropout position.
    assert _plain(status.values["jump"]) == "–"


def test_fix_dropout_ratio_flags_intermittent_loss() -> None:
    plugin = GnssPlugin()
    for i in range(20):
        status_code = NavSatStatus.STATUS_NO_FIX if i % 4 == 0 else NavSatStatus.STATUS_FIX
        plugin.on_message("/gps/fix", _fix(status=status_code, stamp_sec=i))
    status = plugin.get_status()
    # 5/20 = 0.25 dropout ratio > orange 0.2.
    assert "fix dropout ratio" in status.message
    assert _plain(status.values["fixes"]) == "▯▮▮▮" * 5 + "  5/20 dropped"
    assert "GNSS-003" in status.codes


def test_fix_headline_leads_and_position_is_dim_context() -> None:
    plugin = GnssPlugin()
    plugin.on_message("/gps/fix", _fix(lat=45.40642131, lon=11.87683212, alt=31.24))
    values = plugin.get_status().values
    assert next(iter(values)) == "fix"
    assert values["position"] == "[dim]lat 45.4064213  lon 11.8768321  alt 31.2 m[/dim]"


def test_gbas_fix_is_shortened_in_the_topic_panel() -> None:
    plugin = GnssPlugin()
    plugin.on_message("/gps/fix", _fix(status=NavSatStatus.STATUS_GBAS_FIX, covariance=(0.04, 0, 0, 0, 0.0276, 0, 0, 0, 0)))
    assert _plain(plugin.get_status().topic_panel_value) == "GBAS ±0.26"


def test_trends_sample_once_a_second_and_services_are_named() -> None:
    plugin = GnssPlugin()
    msg = _fix(covariance=(0.0001, 0, 0, 0, 0.0001, 0, 0, 0, 0.0001))
    msg.status.service = NavSatStatus.SERVICE_GPS | NavSatStatus.SERVICE_GALILEO
    plugin.on_message("/gps/fix", msg)
    for t in (0.0, 0.5, 1.0, 2.0):
        plugin.on_tick(t)
    values = plugin.get_status().values
    assert _plain(values["σh_more"]) == "███ (3 s)"  # steady, scaled from 0
    assert values["services_more"] == "GPS+GAL"


def test_default_thresholds_cover_expected_metrics() -> None:
    thresholds = GnssPlugin.default_thresholds()
    assert set(thresholds) == {
        "position_covariance",
        "stuck_count",
        "invalid_ratio",
        "jump_speed_mps",
        "fix_dropout_ratio",
    }
