"""Writes exclude rules added or removed live back to the loaded config file.

Text-level, editing only the `exclude_topics:` block, so comments elsewhere
in the file survive (a full YAML re-dump would drop them).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from testudo.core.config import ExcludeMatchType, ExcludeRule


def _render_exclude_rule(rule: ExcludeRule) -> Any:
    """The YAML-serializable form of `rule`: a plain string for `literal`, a mapping for `regex`."""
    if rule.match_type is ExcludeMatchType.LITERAL:
        return rule.pattern
    return {"pattern": rule.pattern, "type": rule.match_type.value}


def write_exclude_rules(path: str | Path, rules: list[ExcludeRule]) -> None:
    """Rewrite `path`'s `exclude_topics:` block to exactly `rules`, leaving every other line untouched.

    Backs the Options screen's "exclude rules are persistent" promise: a
    rule added (or removed) live is written straight back to the config
    file that was loaded at startup, so it survives a restart without the
    user hand-editing YAML. Round-tripping the *whole* document through a
    YAML dumper would silently drop every comment in it (PyYAML doesn't
    preserve them) -- so only the `exclude_topics:` block itself is
    replaced (or removed, if `rules` is empty, or appended fresh if the
    file doesn't have one yet); comments anywhere else in the file survive.

    Creates `path` (and any missing parent directories) if it doesn't
    exist yet -- the default config location is a per-user file Testudo
    never requires to exist up front (an absent one just means "defaults,
    nothing declared or excluded"), so the first exclude rule added live
    from a fresh install needs somewhere to land.
    """
    path = Path(path)
    text = path.read_text() if path.exists() else ""
    if rules:
        block = yaml.safe_dump({"exclude_topics": [_render_exclude_rule(rule) for rule in rules]}, sort_keys=False)
    else:
        block = ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_replace_top_level_block(text, "exclude_topics", block))


def _replace_top_level_block(text: str, key: str, replacement_block: str) -> str:
    """Swap the `key:`-headed block in `text` for `replacement_block` (empty string removes it).

    A block runs from the `key:` line up to (not including) the next
    unindented, non-blank, non-list-item line -- i.e. the next top-level
    key -- or end of file. `key` must start the line at column 0 to
    match, so a commented-out `# key:` line (as in example_config.yaml)
    is correctly left alone. A line starting with `-` at column 0 is
    still part of *this* block, not a new key: `yaml.safe_dump` (what
    `write_exclude_rules` itself uses to build `replacement_block`)
    renders a mapping's list value with its `-` items at the *same*
    indentation as the key, not nested under it -- treating that as
    "end of block" used to strip the `key:` header while leaving its own
    list items behind, orphaned with no header above them.
    """
    lines = text.splitlines(keepends=True)
    start = None
    end = len(lines)
    for i, line in enumerate(lines):
        if start is None:
            if line.startswith(f"{key}:"):
                start = i
            continue
        if line.strip() and not line[0].isspace() and not line.startswith("-"):
            end = i
            break
    if start is None:
        if not replacement_block:
            return text
        if not text:
            return replacement_block
        prefix = text if text.endswith("\n") else text + "\n"
        if prefix.strip() and not prefix.endswith("\n\n"):
            prefix += "\n"
        return prefix + replacement_block
    return "".join(lines[:start]) + replacement_block + "".join(lines[end:])
