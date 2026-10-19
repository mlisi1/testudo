"""Detail Panel tree view for `TFWatchPlugin`: pure functions, no state.

The largest tree, drawn from its root: a run of single-child frames is one
line (`utm ─ map ─ odom ─ base_link`), the first frame with several
children branches, and each branch follows its own single-child run. A
branch that splits again is folded into its frame count, so the view stays
a handful of lines however big the robot's tree is:

```
utm ─ map ─ odom ─ base_link ┬ chassis
                             ├ velodyne ─ velodyne_holder
                             ├ front_camera_link (4)
                             └ … (9 more)
```
"""
from __future__ import annotations

#: Branches drawn under the first split before the rest become "… (N more)".
MAX_BRANCHES = 4


def _run(start: str, children: dict[str, list[str]], seen: set[str]) -> list[str]:
    """`start` and its single-child descendants, stopping at a split, a leaf, or a frame already drawn."""
    run = [start]
    seen.add(start)
    while True:
        below = [child for child in children.get(run[-1], []) if child not in seen]
        if len(below) != 1:
            return run
        run.append(below[0])
        seen.add(below[0])


def _subtree_size(frame: str, children: dict[str, list[str]], seen: set[str]) -> int:
    """How many frames sit below `frame`, not counting ones already drawn (a multi-parent frame counts once)."""
    count = 0
    stack = [frame]
    visited = {frame}
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in visited and child not in seen:
                visited.add(child)
                count += 1
                stack.append(child)
    return count


def _branch_label(start: str, children: dict[str, list[str]], seen: set[str]) -> str:
    """One branch: its single-child run, plus how many frames hang below it when it splits again."""
    run = _run(start, children, seen)
    label = " ─ ".join(run)
    hidden = _subtree_size(run[-1], children, seen)
    return f"{label} [dim]({hidden})[/dim]" if hidden else label


def tree_lines(root: str, children: dict[str, list[str]]) -> list[str]:
    """The tree under `root` as display lines (see the module docstring); `children` lists each frame's children."""
    seen: set[str] = set()
    spine = _run(root, children, seen)
    head = " ─ ".join(spine)
    branches = sorted(child for child in children.get(spine[-1], []) if child not in seen)
    if not branches:
        return [head]

    shown = branches[:MAX_BRANCHES]
    folded = branches[MAX_BRANCHES:]
    labels = [_branch_label(branch, children, seen) for branch in shown]
    if folded:
        folded_frames = len(folded) + sum(_subtree_size(branch, children, seen) for branch in folded)
        labels.append(f"[dim]… ({folded_frames} more)[/dim]")

    indent = " " * (len(head) + 1)
    lines = []
    for index, label in enumerate(labels):
        last = index == len(labels) - 1
        if index == 0:
            glyph = "─" if last else "┬"
            lines.append(f"{head} {glyph} {label}")
        else:
            lines.append(f"{indent}{'└' if last else '├'} {label}")
    return lines
