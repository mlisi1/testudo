"""Edits a YAML config's text in place, leaving every line it doesn't have to change byte for byte.

A YAML dumper can't do this: PyYAML drops comments, and even a round-trip
dumper reindents the file, realigns comments and loses the comment above a
removed block's neighbour. So the document is only parsed to find where things
are (PyYAML's node marks), and the edit is made to the text itself.

A `path` is a tuple of mapping keys and list indices, such as
`("topics", "sensor_msgs/msg/Imu", 0, "codes", "IMU-005")`. Pure functions,
text in and text out.
"""
from __future__ import annotations

from typing import Any

import yaml

from testudo.core.config import ConfigError

Path = tuple[str | int, ...]

#: Keeps a long value on one line when rendering.
_RENDER_WIDTH = 4096


def _parse(text: str) -> yaml.Node | None:
    try:
        return yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise ConfigError(f"can't edit a config that isn't valid YAML: {exc}") from exc


def load_document(text: str) -> dict[str, Any]:
    """The config's top-level mapping (empty for an empty file), to read what's there before editing."""
    root = _parse(text)
    data = _construct(root) if root is not None else {}
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError("can't edit a config whose top level isn't a mapping")
    return data


def _construct(node: yaml.Node) -> Any:
    """The Python value of a parsed node."""
    return yaml.SafeLoader("").construct_document(node)


def _is_block(node: yaml.Node, kind: type) -> bool:
    return isinstance(node, kind) and not node.flow_style and bool(node.value)


def _child(node: yaml.Node, key: str | int) -> tuple[yaml.Node | None, yaml.Node] | None:
    """(key node, value node) of `key` in `node`; a list item has no key node."""
    if isinstance(node, yaml.MappingNode):
        return next(((k, v) for k, v in node.value if k.value == key), None)
    if isinstance(node, yaml.SequenceNode) and isinstance(key, int) and -len(node.value) <= key < len(node.value):
        return None, node.value[key]
    return None


def _find(root: yaml.Node | None, path: Path) -> tuple[yaml.Node | None, yaml.Node] | None:
    """The entry at `path`, or None when it doesn't exist."""
    entry: tuple[yaml.Node | None, yaml.Node] | None = (None, root) if root is not None else None
    for key in path:
        entry = _child(entry[1], key) if entry is not None else None
    return entry


def _last_line(node: yaml.Node, lines: list[str]) -> int:
    """The last line `node`'s content is on; comments after it belong to what follows."""
    if isinstance(node, yaml.CollectionNode) and not node.flow_style and node.value:
        last = node.value[-1]
        return _last_line(last[1] if isinstance(node, yaml.MappingNode) else last, lines)
    line = node.end_mark.line
    if isinstance(node, yaml.ScalarNode) and node.style in ("|", ">"):
        line -= 1 if node.end_mark.column == 0 else 0
        while line > node.start_mark.line and not lines[min(line, len(lines) - 1)].strip():
            line -= 1
    return min(line, len(lines) - 1)


def _render(value: Any, indent: int) -> list[str]:
    """`value` as block-style YAML lines at `indent` columns."""
    text = yaml.safe_dump(value, sort_keys=False, default_flow_style=False, width=_RENDER_WIDTH, allow_unicode=True)
    return [" " * indent + line + "\n" for line in text.splitlines()]


def _nested(path: Path, value: Any) -> Any:
    """`value` wrapped in one mapping per key of `path`."""
    for key in reversed(path):
        value = {key: value}
    return value


def _assign(data: Any, path: Path, value: Any) -> Any:
    """`data` with `value` set at `path`, creating mappings on the way."""
    if not path:
        return value
    if isinstance(data, list) and isinstance(path[0], int):
        data[path[0]] = _assign(data[path[0]], path[1:], value)
        return data
    data = data if isinstance(data, dict) else {}
    data[path[0]] = _assign(data.get(path[0]), path[1:], value)
    return data


def _with_final_newline(lines: list[str]) -> list[str]:
    if lines and not lines[-1].endswith("\n"):
        return [*lines[:-1], lines[-1] + "\n"]
    return lines


def _append_top_level(lines: list[str], block: list[str]) -> str:
    """`block` at the end of the file, set apart by a blank line."""
    lines = _with_final_newline(lines)
    separator = ["\n"] if lines and lines[-1].strip() else []
    return "".join([*lines, *separator, *block])


def set_value(text: str, path: Path, value: Any) -> str:
    """`text` with `value` at `path`, creating what's missing on the way."""
    root = _parse(text)
    lines = text.splitlines(keepends=True)
    if root is None:
        return _append_top_level(lines, _render(_nested(path, value), 0))

    node, key_node = root, None
    for depth, key in enumerate(path):
        if not _is_block(node, yaml.MappingNode) and not _is_block(node, yaml.SequenceNode):
            return _rewrite(text, path, depth, node, value)  # flow style or an empty value: no lines to edit
        child = _child(node, key)
        if child is None:
            return _insert(lines, node, root, path[depth:], value)
        key_node, node = child
    return _replace(lines, key_node, node, path, value)


def _rewrite(text: str, path: Path, depth: int, node: yaml.Node, value: Any) -> str:
    """Set `value` by rewriting the whole of `node`, the entry at `path[:depth]`."""
    data = _assign(_construct(node), path[depth:], value)
    return set_value(text, path[:depth], data) if depth else "".join(_render(data, 0))


def _insert(lines: list[str], parent: yaml.Node, root: yaml.Node, path: Path, value: Any) -> str:
    """A new key at the end of the block mapping `parent`."""
    if not isinstance(parent, yaml.MappingNode):
        raise ConfigError(f"no list item {path[0]} to edit in the config")
    block = _render(_nested(path, value), parent.value[0][0].start_mark.column)
    if parent is root:
        return _append_top_level(lines, block)
    lines = _with_final_newline(lines)
    end = _last_line(parent, lines) + 1
    return "".join([*lines[:end], *block, *lines[end:]])


def _replace(lines: list[str], key_node: yaml.Node | None, node: yaml.Node, path: Path, value: Any) -> str:
    """The existing entry's value becomes `value`: a scalar in place, keeping a comment beside it."""
    written = isinstance(node, yaml.ScalarNode) and node.end_mark.pointer > node.start_mark.pointer
    if written and node.start_mark.line == node.end_mark.line and not isinstance(value, (dict, list)):
        line = lines[node.start_mark.line]
        scalar = yaml.safe_dump(value, default_flow_style=True, width=_RENDER_WIDTH).split("\n...")[0].strip()
        lines[node.start_mark.line] = line[: node.start_mark.column] + scalar + line[node.end_mark.column :]
        return "".join(lines)
    if key_node is None:
        raise ConfigError(f"can't replace the list item at {path} in the config")
    lines = _with_final_newline(lines)
    column = key_node.start_mark.column
    block = _render({path[-1]: value}, column)
    # The first key of a list item shares its line with the item's `-`.
    block[0] = lines[key_node.start_mark.line][:column] + block[0][column:]
    return "".join([*lines[: key_node.start_mark.line], *block, *lines[_last_line(node, lines) + 1 :]])


def _without_lines(lines: list[str], first: int, last: int) -> str:
    """`lines` without first..last, not leaving two blank lines (or a trailing one) at the joint."""
    before, after = lines[:first], lines[last + 1 :]
    if (not after or not after[0].strip()) and (not before or not before[-1].strip()):
        if after:
            after = after[1:]
        elif before:
            before = before[:-1]
    return "".join([*before, *after])


def _item_first_line(item: yaml.Node, lines: list[str]) -> int:
    """The line of the `-` that opens a list item."""
    line = item.start_mark.line
    while line > 0 and not lines[line].lstrip().startswith("-"):
        line -= 1
    return line


def remove_value(text: str, path: Path) -> str:
    """`text` without the entry at `path`, and without any mapping or list that leaves empty."""
    root = _parse(text)
    entry = _find(root, path)
    if entry is None:
        return text
    parent = _find(root, path[:-1])[1]
    if len(parent.value) == 1 and len(path) > 1:
        return remove_value(text, path[:-1])
    if parent.flow_style:
        data = _construct(parent)
        del data[path[-1]]
        return set_value(text, path[:-1], data) if len(path) > 1 else "".join(_render(data, 0))
    lines = text.splitlines(keepends=True)
    key_node, node = entry
    if key_node is None:
        return _without_lines(lines, _item_first_line(node, lines), _last_line(node, lines))
    column = key_node.start_mark.column
    opening = lines[key_node.start_mark.line][:column]
    if opening.strip():  # the first key of a list item: the next key takes over its `-`
        following = parent.value[1][0].start_mark.line
        lines[following] = opening + lines[following][column:]
    return _without_lines(lines, key_node.start_mark.line, _last_line(node, lines))


def sync_list(text: str, path: Path, items: list[Any]) -> str:
    """Make the list at `path` hold `items`: others removed, new ones appended, kept ones untouched."""
    if not items:
        return remove_value(text, path)
    entry = _find(_parse(text), path)
    if entry is None or not _is_block(entry[1], yaml.SequenceNode):
        return set_value(text, path, items)
    existing = [_construct(item) for item in entry[1].value]
    if not any(item in items for item in existing):
        return set_value(text, path, items)
    for index in reversed(range(len(existing))):
        if existing[index] not in items:
            text = remove_value(text, (*path, index))
    return _append_items(text, path, [item for item in items if item not in existing])


def _append_items(text: str, path: Path, items: list[Any]) -> str:
    if not items:
        return text
    sequence = _find(_parse(text), path)[1]
    lines = _with_final_newline(text.splitlines(keepends=True))
    dash = lines[_item_first_line(sequence.value[0], lines)]
    indent = len(dash) - len(dash.lstrip())
    end = _last_line(sequence, lines) + 1
    block = [line for item in items for line in _render([item], indent)]
    return "".join([*lines[:end], *block, *lines[end:]])
