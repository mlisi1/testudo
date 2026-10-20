"""How the screen's height is split between the side-by-side panes (Plugin
Panel | Topic Panel) and the Detail Panel under them.

A fixed share wasted space both ways: a vitals row's three values sat in a
mostly empty Detail Panel while a long Topic Panel scrolled, and a two-topic
category left the Topic Panel empty while a rich Detail Panel was cut off.
So the Detail Panel takes the rows its content needs, and the panes the
rest. Only when both want more than there is does it fall back to
`DETAIL_SHARE`, the old fixed split.

Pure arithmetic, no Textual: `DashboardScreen.refresh_detail` measures,
calls these, and sets the height.
"""
from __future__ import annotations

import math

from rich.text import Text

#: The Detail Panel's share of the height when the panes and the detail
#: both want more than there is -- the fixed split used before this module.
DETAIL_SHARE = 0.45

#: Rows the Detail Panel keeps even when nearly empty (its border plus a
#: header and a couple of values), so it doesn't collapse to a line.
MIN_DETAIL_ROWS = 4

#: Rows the side-by-side panes always keep (a header plus a few rows).
MIN_PANE_ROWS = 4

#: The Detail Panel's border-top: one row its content can't use.
DETAIL_CHROME_ROWS = 1


def rendered_rows(markup: str, width: int) -> int:
    """Screen rows `markup` takes in a `width`-wide pane, counting wrapped lines."""
    width = max(1, width)
    return sum(max(1, math.ceil(Text.from_markup(line).cell_len / width)) for line in markup.split("\n"))


def detail_cap(available: int, panes_wanted: int) -> int:
    """The most rows the Detail Panel may take (border included) out of `available`.

    Whatever the panes leave unused, but never less than `DETAIL_SHARE`
    of the screen (the panes scroll instead), and never so much that the
    panes drop below `MIN_PANE_ROWS`.
    """
    cap = max(available - panes_wanted, round(DETAIL_SHARE * available))
    return max(min(cap, available - MIN_PANE_ROWS), min(MIN_DETAIL_ROWS, available))


def detail_height(cap: int, content_rows: int, previous: int | None = None) -> int:
    """The Detail Panel's height (border included) for `content_rows` of content, within `cap`.

    `previous` is its height for the same topic on the last refresh: the
    panel grows at once but doesn't shrink while the same topic stays
    highlighted, so a code line coming and going doesn't make every pane
    above jump by a row on each poll. Pass `None` when the topic changed.
    """
    wanted = max(content_rows + DETAIL_CHROME_ROWS, MIN_DETAIL_ROWS)
    if previous is not None:
        wanted = max(wanted, previous)
    return min(cap, wanted)
