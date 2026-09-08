"""The "testudo" Textual theme: three colors applied across the TUI's CSS.

- `$primary` (maroon): the Status Bar, the Plugin Panel's row-cursor
  selection, and structural chrome (borders, the help overlay).
- `$accent` (gold): the Topic Panel's row-cursor selection, the nav-tips
  bar, and the Detail Panel's top border -- gold marks "where you are /
  what you're looking at", distinct from the Plugin Panel's red selection.
- `TESTUDO_NEUTRAL` (slate): DataTable column headers only -- a third,
  deliberately unrelated tone so headers read as structural chrome rather
  than "belonging" to either the Status Bar's red or a selection's gold.
  Applied as a literal hex in `TestudoApp.CSS`, not a `$`-variable: a
  custom variable name isn't resolvable at the initial stylesheet parse,
  which happens before `on_mount` registers this theme, against whichever
  theme Textual starts with by default.

Deliberately does not touch severity colors (`SEVERITY_COLORS` in
`testudo.plugins.base`): those are literal Rich markup (green/yellow/red/
magenta) for OK/WARN/ERROR/STALE and must stay visually distinct from the
brand palette, not themed by it.
"""
from __future__ import annotations

from textual.theme import Theme

#: Shield maroon.
TESTUDO_MAROON = "#971b2f"

#: Shield gold.
TESTUDO_GOLD = "#ffb500"

#: Slate -- a third, deliberately neutral tone (no red or gold hue) for
#: DataTable column headers. Kept in sync by hand with the literal hex in
#: `TestudoApp.CSS` (see module docstring for why it can't be a
#: `$`-variable there).
TESTUDO_NEUTRAL = "#4b5563"

TESTUDO_THEME = Theme(
    name="testudo",
    primary=TESTUDO_MAROON,
    accent=TESTUDO_GOLD,
    dark=True,
)
