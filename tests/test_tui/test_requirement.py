from __future__ import annotations

from testudo.tui.requirement import INSTALL_COMMAND, textual_problem


def test_a_recent_textual_is_fine() -> None:
    assert textual_problem("6.0.0") is None
    assert textual_problem("8.2.8") is None
    assert textual_problem("10.1") is None


def test_ubuntu_s_old_textual_is_refused_with_the_install_command() -> None:
    problem = textual_problem("0.1.13")
    assert problem is not None
    assert "0.1.13" in problem and INSTALL_COMMAND in problem


def test_a_prerelease_is_judged_by_its_numbers() -> None:
    assert textual_problem("5.3.0") is not None
    assert textual_problem("6.0.0rc1") is None


def test_a_missing_textual_says_so() -> None:
    assert "isn't installed" in textual_problem("")


def test_the_installed_textual_passes() -> None:
    # The suite itself runs the dashboard, so whatever runs it must be new enough.
    assert textual_problem() is None
