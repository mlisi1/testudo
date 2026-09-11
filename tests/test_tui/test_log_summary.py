"""Unit tests for the log-buffering used to keep a live TUI session from flickering."""
from __future__ import annotations

import logging

import pytest

from testudo.tui.log_summary import LogSummaryHandler, suppressed_terminal_logging


def test_log_summary_handler_ignores_records_below_warning() -> None:
    handler = LogSummaryHandler()
    logger = logging.getLogger("testudo.test_log_summary")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)

    logger.info("just letting you know")
    logger.debug("very quiet")

    assert handler.summary_lines() == []


def test_log_summary_handler_deduplicates_and_counts_repeats() -> None:
    handler = LogSummaryHandler()
    logger = logging.getLogger("testudo.test_log_summary")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)

    logger.warning("could not resolve message class for '%s' (%s)", "/scan", "foo_msgs/msg/Bar")
    logger.warning("could not resolve message class for '%s' (%s)", "/scan", "foo_msgs/msg/Bar")
    logger.error("something else entirely")

    lines = handler.summary_lines()
    assert len(lines) == 2
    # Worst severity first.
    assert lines[0].startswith("ERROR:")
    assert "something else entirely" in lines[0]
    assert lines[1].startswith("WARNING:")
    assert "could not resolve message class for '/scan' (foo_msgs/msg/Bar)" in lines[1]
    assert "(x2)" in lines[1]


def test_log_summary_handler_drops_traceback_from_logger_exception() -> None:
    """`logger.exception(...)` (used throughout subscription_manager.py) attaches a traceback --
    the summary is meant to stay to one short line per distinct message, not reproduce it."""
    handler = LogSummaryHandler()
    logger = logging.getLogger("testudo.test_log_summary")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)

    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("could not resolve message class for '%s'", "/scan")

    lines = handler.summary_lines()
    assert len(lines) == 1
    assert "Traceback" not in lines[0]
    assert lines[0] == "ERROR: could not resolve message class for '/scan'"


def test_suppressed_terminal_logging_restores_previous_handlers() -> None:
    root_logger = logging.getLogger()
    sentinel_handler = logging.NullHandler()
    original_handlers = root_logger.handlers[:]
    root_logger.handlers = [sentinel_handler]
    try:
        with suppressed_terminal_logging() as handler:
            assert root_logger.handlers == [handler]
            assert sentinel_handler not in root_logger.handlers

        assert root_logger.handlers == [sentinel_handler]
    finally:
        root_logger.handlers = original_handlers


def _propagating_test_logger(name: str) -> logging.Logger:
    """A fresh named logger, forced (with every dotted ancestor) to propagate to root.

    Real Testudo code (`_logger = logging.getLogger("testudo")` and
    friends) relies on the ordinary Python default of `propagate=True` to
    reach the root logger's handlers -- which is exactly the mechanism
    `suppressed_terminal_logging` swaps out. Pytest itself, though,
    defaults *every* logger it creates to `propagate=False` as part of
    its own log-capture isolation -- including intermediate ancestors
    (e.g. plain `getLogger("testudo")`, created the moment any test in
    the session imports `testudo.cli`), which silently blocks the climb
    to root partway even after fixing the leaf logger alone. Forcing it
    back to the realistic value at every level is what actually restores
    the non-pytest behavior these tests mean to exercise.
    """
    segments = name.split(".")
    for i in range(len(segments)):
        logging.getLogger(".".join(segments[: i + 1])).propagate = True
    return logging.getLogger(name)


def test_suppressed_terminal_logging_hides_records_from_the_original_handler() -> None:
    root_logger = logging.getLogger()
    sentinel_handler = logging.Handler()
    received: list[logging.LogRecord] = []
    sentinel_handler.emit = received.append  # type: ignore[method-assign]
    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level
    root_logger.handlers = [sentinel_handler]
    root_logger.setLevel(logging.WARNING)
    try:
        with suppressed_terminal_logging():
            _propagating_test_logger("testudo.somewhere").warning("this must not reach the real handlers")
        # Back to normal after the `with` block -- this one *should* reach it.
        _propagating_test_logger("testudo.somewhere").warning("this one is fine")
    finally:
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)

    assert len(received) == 1
    assert received[0].getMessage() == "this one is fine"


def test_suppressed_terminal_logging_prints_nothing_when_nothing_was_caught(capsys: pytest.CaptureFixture) -> None:
    with suppressed_terminal_logging():
        pass
    captured = capsys.readouterr()
    assert captured.out == ""


def test_suppressed_terminal_logging_prints_a_summary_after_the_block(capsys: pytest.CaptureFixture) -> None:
    with suppressed_terminal_logging():
        _propagating_test_logger("testudo.somewhere").warning("could not resolve message class for '%s'", "/scan")

    captured = capsys.readouterr()
    assert "suppressed during the session" in captured.out
    assert "could not resolve message class for '/scan'" in captured.out
