"""Unit tests for LifecycleTracker -- pure state, no live ROS graph needed."""
from __future__ import annotations

from testudo.core.lifecycle import LifecycleTracker


def test_unknown_node_is_not_suppressed() -> None:
    tracker = LifecycleTracker()
    assert tracker.state_of("/controller_server") is None
    assert tracker.is_suppressed("/controller_server") is False


def test_active_node_is_not_suppressed() -> None:
    tracker = LifecycleTracker()
    tracker.on_transition_event("/controller_server", "active")
    assert tracker.state_of("/controller_server") == "active"
    assert tracker.is_suppressed("/controller_server") is False


def test_inactive_node_is_suppressed() -> None:
    tracker = LifecycleTracker()
    tracker.on_transition_event("/controller_server", "inactive")
    assert tracker.is_suppressed("/controller_server") is True


def test_unconfigured_and_finalized_are_suppressed() -> None:
    tracker = LifecycleTracker()
    tracker.on_transition_event("/a", "unconfigured")
    tracker.on_transition_event("/b", "finalized")
    assert tracker.is_suppressed("/a") is True
    assert tracker.is_suppressed("/b") is True


def test_state_updates_on_new_transition() -> None:
    tracker = LifecycleTracker()
    tracker.on_transition_event("/controller_server", "inactive")
    assert tracker.is_suppressed("/controller_server") is True
    tracker.on_transition_event("/controller_server", "active")
    assert tracker.is_suppressed("/controller_server") is False


def test_nodes_are_tracked_independently() -> None:
    tracker = LifecycleTracker()
    tracker.on_transition_event("/a", "active")
    tracker.on_transition_event("/b", "inactive")
    assert tracker.is_suppressed("/a") is False
    assert tracker.is_suppressed("/b") is True
