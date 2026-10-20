"""Unit tests for writing live exclude rules back to a config file."""
from __future__ import annotations

from pathlib import Path

from testudo.core.config import ExcludeMatchType, ExcludeRule
from testudo.core.config_loader import load_config
from testudo.core.config_writer import write_exclude_rules


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


def test_write_exclude_rules_appends_new_block_and_preserves_comments(tmp_path: Path) -> None:
    path = _write(tmp_path, "# a comment\nseverity_mode: worst\n")
    write_exclude_rules(path, [ExcludeRule("/velodyne_packets")])
    text = path.read_text()
    assert "# a comment" in text
    assert "severity_mode: worst" in text
    config = load_config(path)
    assert config.exclude_topics == [ExcludeRule("/velodyne_packets")]


def test_write_exclude_rules_replaces_existing_block_only(tmp_path: Path) -> None:
    # Un-indented list items ("- /old_topic", not "  - /old_topic"): this is
    # what `yaml.safe_dump` (and thus `write_exclude_rules` itself) actually
    # produces for a mapping's list value -- items at the *same* column as
    # the key, not nested under it. A hand-written fixture using 2-space
    # indentation here would pass even with the header/body-splitting bug
    # this regression guards against (see the multi-write test below).
    path = _write(
        tmp_path,
        "# top comment\ntopics: {}\nexclude_topics:\n- /old_topic\nseverity_mode: worst\n",
    )
    write_exclude_rules(path, [ExcludeRule("/new_topic"), ExcludeRule("_debug$", ExcludeMatchType.REGEX)])
    text = path.read_text()
    assert "# top comment" in text
    assert "severity_mode: worst" in text
    assert "/old_topic" not in text
    config = load_config(path)
    assert config.exclude_topics == [ExcludeRule("/new_topic"), ExcludeRule("_debug$", ExcludeMatchType.REGEX)]


def test_write_exclude_rules_survives_a_commented_example_block_above_it(tmp_path: Path) -> None:
    """Regression: a commented `# exclude_topics:` example (as in example_config.yaml) sits
    above the real block -- the real header must still be found and cleanly replaced/removed."""
    path = _write(
        tmp_path,
        "severity_mode: worst\n"
        "# exclude_topics:\n"
        "#   - /velodyne_packets\n"
        "#   - pattern: \"_debug$\"\n"
        "#     type: regex\n"
        "\n"
        "exclude_topics:\n"
        "- pattern: _packets$\n"
        "  type: regex\n",
    )
    write_exclude_rules(path, [])  # remove the (only) rule
    text = path.read_text()
    assert "# exclude_topics:" in text  # the commented example is untouched
    assert "severity_mode: worst" in text
    config = load_config(path)
    assert config.exclude_topics == []


def test_write_exclude_rules_repeated_add_then_remove_round_trips_cleanly(tmp_path: Path) -> None:
    """Regression: this exact add-then-remove sequence (via the Options screen) used to leave
    a header-less, orphaned list fragment behind -- see `_replace_top_level_block`'s docstring."""
    path = _write(tmp_path, "severity_mode: worst\n")

    write_exclude_rules(path, [ExcludeRule("_packets$", ExcludeMatchType.REGEX)])
    assert load_config(path).exclude_topics == [ExcludeRule("_packets$", ExcludeMatchType.REGEX)]

    write_exclude_rules(path, [])  # remove it
    text = path.read_text()
    assert "exclude_topics" not in text
    assert "pattern" not in text  # no orphaned list body left behind
    assert load_config(path).exclude_topics == []

    write_exclude_rules(path, [ExcludeRule("/velodyne_packets")])  # add a different one after
    assert load_config(path).exclude_topics == [ExcludeRule("/velodyne_packets")]
    assert path.read_text().count("exclude_topics:") == 1  # sanity: not accumulating duplicate headers


def test_write_exclude_rules_with_empty_list_removes_the_block(tmp_path: Path) -> None:
    path = _write(tmp_path, "exclude_topics:\n- /old_topic\nseverity_mode: worst\n")
    write_exclude_rules(path, [])
    text = path.read_text()
    assert "exclude_topics" not in text
    assert "/old_topic" not in text
    assert "severity_mode: worst" in text


def test_write_exclude_rules_creates_a_missing_file_and_its_parent_directory(tmp_path: Path) -> None:
    """The default config location is a per-user file Testudo never requires to exist up
    front -- the first exclude rule added live (e.g. from a fresh install) needs somewhere
    to land rather than crashing on a `FileNotFoundError`."""
    path = tmp_path / "nested" / "does" / "not" / "exist" / "config.yaml"
    assert not path.parent.exists()

    write_exclude_rules(path, [ExcludeRule("/velodyne_packets")])

    assert load_config(path).exclude_topics == [ExcludeRule("/velodyne_packets")]
    assert not path.read_text().startswith("\n")  # no artificial leading blank line either
