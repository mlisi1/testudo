"""What rate Nav2's own parameters say a topic should publish at, read from the running servers.

Observe only: a parameter read is the one request made of these nodes, and
nothing happens on a stack without them. Each rule is a parameter that really
sets a topic's period, checked against a real server:

- `controller_server.controller_frequency`: its velocity command;
- `velocity_smoother.smoothing_frequency`: its smoothed command;
- a costmap's `publish_frequency`: its full grid, only with
  `always_send_full_costmap` (otherwise it publishes once and then sends
  updates only when something changes). It publishes on the first update
  cycle after the period is over, so the rate it can reach also depends on
  `update_frequency`: 2 Hz asked of a 5 Hz update loop comes out at 1.67 Hz.

These topics go quiet by design (a parked robot gets no commands, a costmap
is latched), so the rate is only judged while the topic is actually publishing.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from rcl_interfaces.srv import GetParameters
from rclpy.parameter import parameter_value_to_python

from testudo.core.vitals import TopicVitals
from testudo.plugins.base import Severity, SeverityLevels, evaluate_levels

_logger = logging.getLogger(__name__)

_COMMAND_TYPES = frozenset({"geometry_msgs/msg/Twist", "geometry_msgs/msg/TwistStamped"})
_GRID_TYPE = "nav_msgs/msg/OccupancyGrid"

_FREQUENCY_BY_SERVER = {"controller_server": "controller_frequency", "velocity_smoother": "smoothing_frequency"}
_COSTMAP_FREQUENCY = "publish_frequency"
_COSTMAP_UPDATE_FREQUENCY = "update_frequency"
_COSTMAP_FULL_GRID = "always_send_full_costmap"

#: Below these shares of the configured rate a topic is WARN and ERROR.
_WARN_SHARE = 0.8
_ERROR_SHARE = 0.5
#: The rate is measured over this long, or this many periods if that's longer.
_MIN_WINDOW_SECONDS = 2.0
_WINDOW_PERIODS = 5.0

#: How often a server is read again (a restart may change its parameters), and retried until it answers.
_REFRESH_SECONDS = 30.0
_RETRY_SECONDS = 5.0
_RESPONSE_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class ExpectedRate:
    """The rate a topic should publish at, and the parameter that says so."""

    hz: float
    source: str


def _is_costmap(base_name: str) -> bool:
    return base_name.endswith("_costmap")


def parameters_to_read(node_name: str) -> list[str]:
    """The parameters worth reading from `node_name`; empty for a node no rule covers."""
    base_name = node_name.rsplit("/", 1)[-1]
    if base_name in _FREQUENCY_BY_SERVER:
        return [_FREQUENCY_BY_SERVER[base_name]]
    return [_COSTMAP_FREQUENCY, _COSTMAP_UPDATE_FREQUENCY, _COSTMAP_FULL_GRID] if _is_costmap(base_name) else []


def _is_frequency(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _costmap_rate(publish_frequency: float, update_frequency: float) -> float:
    """What a costmap really publishes at: a whole number of update cycles per publish."""
    cycles = math.ceil(round(update_frequency / publish_frequency, 6))
    return update_frequency / max(1, cycles)


def expected_rates(node_name: str, parameters: dict[str, Any], published: dict[str, list[str]]) -> dict[str, ExpectedRate]:
    """Topic -> expected rate, from one node's parameters and the topics it publishes (name -> types)."""
    base_name = node_name.rsplit("/", 1)[-1]
    if base_name in _FREQUENCY_BY_SERVER:
        source = _FREQUENCY_BY_SERVER[base_name]
        topics = [topic for topic, types in published.items() if _COMMAND_TYPES.intersection(types)]
    elif _is_costmap(base_name) and parameters.get(_COSTMAP_FULL_GRID) is True:
        source = _COSTMAP_FREQUENCY
        topics = [topic for topic, types in published.items() if _GRID_TYPE in types]
    else:
        return {}
    frequency = parameters.get(source)
    if not _is_frequency(frequency):
        return {}
    if source == _COSTMAP_FREQUENCY:
        update_frequency = parameters.get(_COSTMAP_UPDATE_FREQUENCY)
        if not _is_frequency(update_frequency):
            return {}
        frequency = _costmap_rate(frequency, update_frequency)
    return {topic: ExpectedRate(float(frequency), source) for topic in topics}


def rate_problem(expected: ExpectedRate, vitals: TopicVitals, now: float) -> tuple[int, str] | None:
    """(severity, message) when a topic publishing right now is slower than `expected`, else None."""
    rate = vitals.recent_rate_hz(now, max(_MIN_WINDOW_SECONDS, _WINDOW_PERIODS / expected.hz))
    if rate is None:
        return None
    levels = SeverityLevels(warn=_WARN_SHARE * expected.hz, error=_ERROR_SHARE * expected.hz)
    severity = evaluate_levels(rate, levels, higher_is_worse=False)
    if severity == Severity.OK:
        return None
    return severity, f"rate {rate:.1f} Hz, {expected.source} asks for {expected.hz:.3g} Hz"


class Nav2RateReader:
    """Keeps `expected` current from the Nav2 servers on the graph, without ever blocking.

    `poll` must run on the thread spinning the node. `expected` is replaced
    whole, so any thread may read it.
    """

    def __init__(self, node: Any) -> None:
        self._node = node
        self.expected: dict[str, ExpectedRate] = {}
        self._rates_by_node: dict[str, dict[str, ExpectedRate]] = {}
        self._next_read: dict[str, float] = {}
        self._pending: dict[str, tuple[Any, Any, float, list[str], str, str]] = {}
        self._next_discovery = 0.0

    def poll(self, now: float) -> None:
        """Collect finished reads and, every few seconds, start the ones that are due."""
        for full_name in list(self._pending):
            self._collect(full_name, now)
        if now < self._next_discovery:
            return
        self._next_discovery = now + _RETRY_SECONDS
        present: set[str] = set()
        for name, namespace in self._node.get_node_names_and_namespaces():
            full_name = f"{namespace.rstrip('/')}/{name}"
            names = parameters_to_read(full_name)
            if names:
                present.add(full_name)
                if full_name not in self._pending and now >= self._next_read.get(full_name, 0.0):
                    self._start_read(full_name, name, namespace, names, now)
        for gone in set(self._rates_by_node) - present:
            del self._rates_by_node[gone]
            self._next_read.pop(gone, None)
            self._publish()

    def _start_read(self, full_name: str, name: str, namespace: str, names: list[str], now: float) -> None:
        client = self._node.create_client(GetParameters, f"{full_name}/get_parameters")
        if not client.service_is_ready():
            self._node.destroy_client(client)
            self._next_read[full_name] = now + _RETRY_SECONDS
            return
        future = client.call_async(GetParameters.Request(names=names))
        self._pending[full_name] = (client, future, now, names, name, namespace)

    def _collect(self, full_name: str, now: float) -> None:
        client, future, started, names, name, namespace = self._pending[full_name]
        if not future.done() and now - started < _RESPONSE_TIMEOUT_SECONDS:
            return
        del self._pending[full_name]
        response = future.result() if future.done() else None
        self._node.destroy_client(client)
        rates: dict[str, ExpectedRate] = {}
        if response is not None:
            values = {key: parameter_value_to_python(value) for key, value in zip(names, response.values)}
            rates = expected_rates(full_name, values, self._published_by(name, namespace))
        self._next_read[full_name] = now + (_REFRESH_SECONDS if rates else _RETRY_SECONDS)
        self._rates_by_node[full_name] = rates
        self._publish()

    def _published_by(self, name: str, namespace: str) -> dict[str, list[str]]:
        try:
            return dict(self._node.get_publisher_names_and_types_by_node(name, namespace))
        except Exception:  # the node left the graph between the two calls
            _logger.debug("could not list the publishers of %s/%s", namespace, name, exc_info=True)
            return {}

    def _publish(self) -> None:
        self.expected = {topic: rate for rates in self._rates_by_node.values() for topic, rate in rates.items()}
