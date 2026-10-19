"""TF watch plugin: missing declared chains, multi-parent frames, and stale edges.

Bound to `/tf` (dynamic transforms), with `/tf_static` routed in as a
related topic feeding the same instance. Builds an undirected adjacency
graph from every observed (parent, child) edge to check connectivity for
declared frame pairs and to detect a child frame with more than one
distinct parent -- an invalid TF tree.

This approximates tf2's extrapolation-exception concept (a lookup outside
the buffered time range) as "this edge hasn't updated within tf2's default
~10s cache window", rather than running a real `tf2_ros.Buffer` -- which
maintains its own independent subscription outside Testudo's two-tier
model and would double-subscribe `/tf`.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from testudo.plugins.base import SEVERITY_COLORS, CheckPlugin, CheckStatus, Severity, ThresholdZone, colorize

#: tf2's default transform buffer duration; used here only as the
#: staleness window for the extrapolation-risk approximation.
DEFAULT_BUFFER_WINDOW_SECONDS = 10.0

#: How many roots / multi-parent frames / stale edges a one-line value
#: names before summarizing the rest as "+N more".
_MAX_LISTED = 3


def _listed(items: list[str]) -> str:
    """`items` joined with " · ", at most `_MAX_LISTED` of them, or a dim "–" when there are none."""
    if not items:
        return "[dim]–[/dim]"
    shown = " · ".join(items[:_MAX_LISTED])
    hidden = len(items) - _MAX_LISTED
    return f"{shown} [dim]+{hidden} more[/dim]" if hidden > 0 else shown


class TFWatchPlugin(CheckPlugin):
    """Watches /tf (+/tf_static) for missing chains, multi-parent frames, and stale edges."""

    def __init__(
        self,
        thresholds: dict[str, ThresholdZone] | None = None,
        related_topics: dict[str, str] | None = None,
        buffer_window_seconds: float = DEFAULT_BUFFER_WINDOW_SECONDS,
    ) -> None:
        super().__init__(thresholds, related_topics)
        self._buffer_window_seconds = buffer_window_seconds
        self._watched_pairs: list[tuple[str, str]] = []
        self._parents_by_child: dict[str, set[str]] = defaultdict(set)
        self._adjacency: dict[str, set[str]] = defaultdict(set)
        self._last_seen_seconds: dict[tuple[str, str], float] = {}
        # Edges only ever seen on /tf_static: latched, published once, and
        # valid forever by definition -- never aged by `_stale_edges`.
        self._static_edges: set[tuple[str, str]] = set()
        self._now = 0.0
        self._edge_count = 0

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return ("tf2_msgs/msg/TFMessage",)

    @classmethod
    def default_thresholds(cls) -> dict[str, ThresholdZone]:
        return {}

    def set_watched_pairs(self, pairs: list[tuple[str, str]]) -> None:
        """Declare which (parent, child) pairs to check for a connected chain.

        Called by SubscriptionManager (not part of the CheckPlugin ABC --
        TF's config comes from the dedicated `tf:` section, not a topic's
        thresholds/related_topics) with the pairs from config.tf.
        """
        self._watched_pairs = list(pairs)

    def reset(self) -> None:
        """Clear the observed tree, keeping the watched pairs and buffer window.

        Both are configuration (set after construction, from `tf:`), not
        accumulated state -- the base class's re-run of `__init__` would
        silently drop the watched pairs, and every chain check with them.
        """
        watched_pairs = self._watched_pairs
        self.__init__(self.thresholds, self.related_topics, self._buffer_window_seconds)
        self._watched_pairs = watched_pairs

    def on_tick(self, now_seconds: float) -> None:
        self._now = now_seconds

    def on_message(self, topic: str, msg: Any) -> None:
        is_static = topic == self.related_topics.get("tf_static")
        for transform in msg.transforms:
            parent = transform.header.frame_id
            child = transform.child_frame_id
            self._edge_count += 1
            self._parents_by_child[child].add(parent)
            self._adjacency[parent].add(child)
            self._adjacency[child].add(parent)
            self._last_seen_seconds[(parent, child)] = self._now
            if is_static:
                self._static_edges.add((parent, child))
            else:
                self._static_edges.discard((parent, child))

    def _multi_parent_children(self) -> dict[str, set[str]]:
        return {child: parents for child, parents in self._parents_by_child.items() if len(parents) > 1}

    def _path(self, a: str, b: str) -> list[str] | None:
        """The frames from `a` to `b` following observed edges in either direction, or None if unconnected."""
        if a not in self._adjacency or b not in self._adjacency:
            return None
        previous: dict[str, str | None] = {a: None}
        queue = [a]
        for node in queue:
            if node == b:
                path = [b]
                while previous[path[-1]] is not None:
                    path.append(previous[path[-1]])
                return path[::-1]
            for neighbor in sorted(self._adjacency[node]):
                if neighbor not in previous:
                    previous[neighbor] = node
                    queue.append(neighbor)
        return None

    def _connected(self, a: str, b: str) -> bool:
        """Whether `b` is reachable from `a` by following observed edges in either direction."""
        return self._path(a, b) is not None

    def _missing_pairs(self) -> list[tuple[str, str]]:
        return [(parent, child) for parent, child in self._watched_pairs if not self._connected(parent, child)]

    def _stale_edges(self) -> list[tuple[str, str]]:
        return [
            edge
            for edge, last_seen in self._last_seen_seconds.items()
            if edge not in self._static_edges and (self._now - last_seen) > self._buffer_window_seconds
        ]

    def _trees(self) -> list[tuple[str, int]]:
        """Each connected tree as (its root, its frame count), largest first.

        A root is a frame no transform names as a child. More than one tree
        is the classic "two TF trees" bug: a lookup across them can never
        succeed. A tree whose every frame has a parent (a cycle) is listed
        under its alphabetically first frame.
        """
        seen: set[str] = set()
        trees = []
        for start in sorted(self._adjacency):
            if start in seen:
                continue
            component = [start]
            seen.add(start)
            for node in component:
                for neighbor in self._adjacency[node]:
                    if neighbor not in seen:
                        seen.add(neighbor)
                        component.append(neighbor)
            roots = sorted(frame for frame in component if not self._parents_by_child.get(frame))
            trees.append((roots[0] if roots else min(component), len(component)))
        return sorted(trees, key=lambda tree: (-tree[1], tree[0]))

    def _hop_arrow(self, a: str, b: str) -> str:
        """"→" if `a` is `b`'s parent, "←" if it's the other way round -- the direction tf2 stores it."""
        return " → " if (a, b) in self._last_seen_seconds else " ← "

    def _chain_line(self, parent: str, child: str) -> tuple[str, str | None]:
        """A watched pair as a ✓/✗ path, and why it's missing (None when it's connected)."""
        path = self._path(parent, child)
        if path is not None:
            hops = "".join(self._hop_arrow(a, b) + b for a, b in zip(path, path[1:]))
            return f"{colorize('✓', Severity.OK)} {path[0]}{hops}", None
        unseen = [frame for frame in (parent, child) if frame not in self._adjacency]
        reason = f"{' and '.join(unseen)} never seen" if unseen else "in separate trees"
        return f"{colorize('✗', Severity.ERROR)} {parent} ⇢ {child}  [dim]{reason}[/dim]", reason

    def get_status(self) -> CheckStatus:
        if self._edge_count == 0:
            return CheckStatus(
                severity=Severity.OK, label="tf", message="no transforms received yet", values={"frames": "none yet"}
            )

        multi_parent = self._multi_parent_children()
        stale = self._stale_edges()
        chains = [(pair, *self._chain_line(*pair)) for pair in self._watched_pairs]
        missing = [(pair, reason) for pair, _line, reason in chains if reason is not None]

        severity = Severity.OK
        problems = []
        codes: dict[str, str] = {}
        if multi_parent:
            severity = Severity.ERROR
            names = ", ".join(sorted(multi_parent))
            problems.append(f"multi-parent frame(s): {names}")
            codes["TF-001"] = colorize(f"multi-parent frame(s): {names}", Severity.ERROR)
        if missing:
            severity = max(severity, Severity.ERROR)
            pairs = ", ".join(f"{parent}->{child}" for (parent, child), _reason in missing)
            problems.append(f"missing chain(s): {pairs}")
            codes["TF-002"] = colorize(
                "; ".join(f"missing chain {parent} → {child} ({reason})" for (parent, child), reason in missing),
                Severity.ERROR,
            )
        if stale:
            severity = max(severity, Severity.WARN)
            problems.append(f"{len(stale)} edge(s) not updated in >{self._buffer_window_seconds:.0f}s (extrapolation risk)")
            codes["TF-003"] = colorize(
                f"{len(stale)} dynamic edge(s) not updated in >{self._buffer_window_seconds:.0f}s", Severity.WARN
            )

        frame_count = len(self._adjacency)
        static_count = len(self._static_edges)
        dynamic_count = len(self._last_seen_seconds) - static_count
        trees = self._trees()
        stale_by_age = sorted(stale, key=lambda edge: self._last_seen_seconds[edge])
        values = {
            "frames": f"{frame_count}  [dim]{dynamic_count} dynamic · {static_count} static edges[/dim]",
            "roots": _listed([f"{root} [dim]({size})[/dim]" for root, size in trees]),
            "multi_parent": _listed(
                [f"{child} ← {', '.join(sorted(parents))}" for child, parents in sorted(multi_parent.items())]
            ),
            "stale_dynamic": _listed(
                [f"{child} {self._now - self._last_seen_seconds[(parent, child)]:.0f}s" for parent, child in stale_by_age]
            ),
        }
        if chains:
            # Broken chains always get a line each; connected ones only a
            # count, with their full paths as a `_more` block -- on a short
            # Detail Panel the paths would push the TF-002 code out of view.
            connected = [line for _pair, line, reason in chains if reason is None]
            broken = [line for _pair, line, reason in chains if reason is not None]
            summary = colorize(f"✓ {len(connected)}/{len(chains)} watched chains connected", Severity.OK)
            values["chains"] = "\n".join(broken + [summary]) if connected else "\n".join(broken)
            if connected:
                values["connected_more"] = "\n".join(connected)

        issues = len(multi_parent) + len(missing) + (1 if stale else 0)
        color = SEVERITY_COLORS.get(severity, "white")
        column = f"{frame_count}" + (f" [{color}]✗{issues}[/{color}]" if issues else "")
        return CheckStatus(
            severity=severity,
            label="tf",
            message="; ".join(problems) if problems else "tf tree nominal",
            values=values,
            topic_panel_column="Frames",
            topic_panel_value=column,
            codes=codes,
        )
