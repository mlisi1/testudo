"""Unit tests for reading expected rates from Nav2 parameters -- a fake node, no live graph."""
from __future__ import annotations

import pytest
from rcl_interfaces.msg import ParameterType, ParameterValue

from testudo.core.nav2_rates import ExpectedRate, Nav2RateReader, expected_rates, parameters_to_read, rate_problem
from testudo.core.vitals import TopicVitals
from testudo.plugins.base import Severity

_TWIST = ["geometry_msgs/msg/Twist"]
_GRID = ["nav_msgs/msg/OccupancyGrid"]


def test_only_known_servers_are_read() -> None:
    assert parameters_to_read("/controller_server") == ["controller_frequency"]
    assert parameters_to_read("/robot1/velocity_smoother") == ["smoothing_frequency"]
    assert parameters_to_read("/local_costmap/local_costmap") == [
        "publish_frequency",
        "update_frequency",
        "always_send_full_costmap",
    ]
    assert parameters_to_read("/amcl") == []


def test_the_controller_frequency_applies_to_its_command_topic() -> None:
    rates = expected_rates("/controller_server", {"controller_frequency": 20.0}, {"/cmd_vel": _TWIST, "/local_plan": ["nav_msgs/msg/Path"]})
    assert rates == {"/cmd_vel": ExpectedRate(20.0, "controller_frequency")}


def test_a_costmap_is_only_periodic_when_it_always_sends_the_full_grid() -> None:
    published = {"/local_costmap/costmap": _GRID}
    full = {"publish_frequency": 2.0, "update_frequency": 4.0, "always_send_full_costmap": True}
    assert expected_rates("/local_costmap/local_costmap", full, published) == {
        "/local_costmap/costmap": ExpectedRate(2.0, "publish_frequency")
    }
    assert expected_rates("/local_costmap/local_costmap", {**full, "always_send_full_costmap": False}, published) == {}


def test_a_costmap_can_only_publish_on_an_update_cycle() -> None:
    # Measured on a real costmap: 2 Hz asked of a 5 Hz update loop comes out at 1.67 Hz.
    parameters = {"publish_frequency": 2.0, "update_frequency": 5.0, "always_send_full_costmap": True}
    rate = expected_rates("/local_costmap/local_costmap", parameters, {"/local_costmap/costmap": _GRID})
    assert rate["/local_costmap/costmap"].hz == pytest.approx(5.0 / 3.0)


def test_a_costmap_cannot_publish_faster_than_it_updates() -> None:
    parameters = {"publish_frequency": 10.0, "update_frequency": 5.0, "always_send_full_costmap": True}
    rate = expected_rates("/local_costmap/local_costmap", parameters, {"/local_costmap/costmap": _GRID})
    assert rate["/local_costmap/costmap"].hz == 5.0


def test_a_missing_or_zero_frequency_says_nothing() -> None:
    assert expected_rates("/controller_server", {"controller_frequency": None}, {"/cmd_vel": _TWIST}) == {}
    assert expected_rates("/controller_server", {"controller_frequency": 0.0}, {"/cmd_vel": _TWIST}) == {}


def _vitals(rate_hz: float, seconds: float, start: float = 100.0) -> tuple[TopicVitals, float]:
    vitals = TopicVitals(topic="/cmd_vel")
    count = int(seconds * rate_hz)
    for index in range(count):
        vitals.record_arrival(start + index / rate_hz)
    return vitals, start + (count - 1) / rate_hz


_EXPECTED = ExpectedRate(20.0, "controller_frequency")


def test_a_topic_at_its_configured_rate_is_fine() -> None:
    vitals, now = _vitals(20.0, 3.0)
    assert rate_problem(_EXPECTED, vitals, now) is None


def test_a_slow_topic_warns_and_a_very_slow_one_errors() -> None:
    vitals, now = _vitals(14.0, 3.0)
    severity, message = rate_problem(_EXPECTED, vitals, now)
    assert severity == Severity.WARN
    assert message == "rate 14.0 Hz, controller_frequency asks for 20 Hz"
    vitals, now = _vitals(5.0, 3.0)
    assert rate_problem(_EXPECTED, vitals, now)[0] == Severity.ERROR


def test_a_topic_that_stopped_publishing_is_not_slow() -> None:
    vitals, now = _vitals(20.0, 3.0)
    assert rate_problem(_EXPECTED, vitals, now + 30.0) is None


def test_starting_again_after_a_pause_is_not_slow() -> None:
    vitals, now = _vitals(20.0, 3.0)
    resumed = now + 30.0
    for index in range(10):
        vitals.record_arrival(resumed + index * 0.05)
    assert rate_problem(_EXPECTED, vitals, resumed + 0.45) is None


class _Future:
    def __init__(self, response: object | None) -> None:
        self._response = response

    def done(self) -> bool:
        return self._response is not None

    def result(self) -> object | None:
        return self._response


class _Client:
    def __init__(self, ready: bool, response: object | None) -> None:
        self._ready, self._response = ready, response

    def service_is_ready(self) -> bool:
        return self._ready

    def call_async(self, request: object) -> _Future:
        return _Future(self._response)


class _Response:
    values = [ParameterValue(type=ParameterType.PARAMETER_DOUBLE, double_value=20.0)]


class _Node:
    def __init__(self, ready: bool = True, response: object | None = _Response()) -> None:
        self.nodes = [("controller_server", "/"), ("amcl", "/")]
        self.ready, self.response = ready, response
        self.created: list[str] = []
        self.destroyed = 0

    def get_node_names_and_namespaces(self) -> list[tuple[str, str]]:
        return self.nodes

    def create_client(self, srv_type: type, name: str) -> _Client:
        self.created.append(name)
        return _Client(self.ready, self.response)

    def destroy_client(self, client: object) -> None:
        self.destroyed += 1

    def get_publisher_names_and_types_by_node(self, name: str, namespace: str) -> list[tuple[str, list[str]]]:
        return [("/cmd_vel", _TWIST)]


def _read(node: _Node) -> Nav2RateReader:
    reader = Nav2RateReader(node)
    reader.poll(100.0)
    reader.poll(100.1)
    return reader


def test_a_running_controller_is_read_once_and_its_client_released() -> None:
    node = _Node()
    assert _read(node).expected == {"/cmd_vel": ExpectedRate(20.0, "controller_frequency")}
    assert node.created == ["/controller_server/get_parameters"]
    assert node.destroyed == 1


def test_a_stack_without_nav2_is_left_alone() -> None:
    node = _Node()
    node.nodes = [("amcl", "/"), ("my_driver", "/")]
    assert _read(node).expected == {}
    assert node.created == []


def test_a_server_not_answering_yet_is_retried_later() -> None:
    node = _Node(ready=False)
    reader = _read(node)
    assert reader.expected == {}
    node.ready = True
    reader.poll(106.0)
    reader.poll(106.1)
    assert "/cmd_vel" in reader.expected


def test_a_read_that_never_completes_times_out() -> None:
    node = _Node(response=None)
    reader = _read(node)
    reader.poll(104.0)
    assert node.destroyed == 1
    assert reader.expected == {}


def test_a_server_that_left_the_graph_takes_its_rates_with_it() -> None:
    node = _Node()
    reader = _read(node)
    node.nodes = []
    reader.poll(106.0)
    assert reader.expected == {}
