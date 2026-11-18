"""The Dashboard page's annotated screenshot: what each thing on screen is and where it is documented.

Positions are terminal cells of `docs/assets/media/tui/dashboard.png`, so a re-recorded
screenshot with the same layout needs no change here. `gen_reference.py` writes the result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from pathlib import Path

IMAGE_WIDTH = 1100
IMAGE_HEIGHT = 640
IMAGE_SRC = "../assets/media/tui/dashboard.png"
CELL_WIDTH = 9
CELL_HEIGHT = 18
#: The last column and row the terminal has in the screenshot.
COLUMNS = 122
ROWS = 35


@dataclass(frozen=True)
class Spot:
    """A rectangle of terminal cells. `target` overrides its item's link; `badge` is where the number sits."""

    col: int
    row: int
    width: int
    height: int = 1
    target: str | None = None
    badge: str | None = "top"


@dataclass(frozen=True)
class Item:
    """One piece of information on screen: `target` is a docs path, None when no page covers it."""

    name: str
    text: str
    target: str | None
    link: str = ""
    spots: tuple[Spot, ...] = ()


@dataclass(frozen=True)
class Crop:
    """The part of the screenshot a figure shows, in cells, and the items marked on it."""

    col: int
    row: int
    width: int
    height: int
    items: tuple[Item, ...] = field(default=())


def _hint_items() -> tuple[Item, ...]:
    """One item per hint bar entry, placed from the bar's own text."""
    entries = (
        ("j/k move", "Move the cursor. The other panes follow.", "tui/keys.md", "Keys"),
        ("tab/←→ pane", "Switch between the categories and the topics.", "tui/keys.md", "Keys"),
        ("/ search", "Find a topic by name, from any category.", "tui/keys.md", "Keys"),
        ("n problem", "Jump to the next topic with an active code.", "tui/keys.md", "Keys"),
        ("a actions", "Mute, downgrade or tune the highlighted topic's code.", "tui/quick-actions.md", "Quick actions"),
        ("p pause", "Freeze the display. Checks and publishing continue.", "tui/keys.md", "Keys"),
        ("o options", "Exclude topics, change check settings and display preferences.", "tui/options.md", "Options"),
        ("? help", "Show every key.", "tui/keys.md", "Keys"),
        ("q quit", "Close the dashboard.", "tui/keys.md", "Keys"),
    )
    items = []
    col = 1
    for label, text, target, link in entries:
        items.append(Item(f"`{label}`", text, target, link, (Spot(col, 34, len(label)),)))
        col += len(label) + len(" · ")
    return tuple(items)


#: The Plugin Panel's rows, top to bottom, as `dashboard.png` shows them: check them after re-recording it.
_CATEGORIES = (
    ("plugins/image-stream.md", "Image stream"),
    ("plugins/gnss.md", "GNSS"),
    ("plugins/tf-watch.md", "TF watch"),
    ("plugins/imu.md", "IMU"),
    ("plugins/actions.md", "Actions"),
    ("plugins/bt-log.md", "Behavior tree log"),
    ("plugins/point-stream.md", "Point stream"),
    ("plugins/localization.md", "Localization"),
    ("plugins/costmap.md", "Costmap"),
    ("core-checks/node.md", "Node profiling"),
    ("plugins/velocity-command.md", "Velocity command"),
)

STATUS_LEFT = Crop(0, 0, 64, 1, (
    Item("Name", "The tool you are looking at.", "index.md", "Home", (Spot(1, 0, 7),)),
    Item("ROS distro", "The ROS 2 distribution this session runs on.", "getting-started/index.md#1-install", "Install",
         (Spot(10, 0, 9),)),
    Item("Domain", "The `ROS_DOMAIN_ID`. Testudo only sees topics on this domain.",
         "getting-started/index.md#2-watch-your-stack", "Watch your stack", (Spot(22, 0, 10),)),
    Item("Middleware", "The RMW implementation in use. Hidden when the terminal is under 120 columns.", None,
         spots=(Spot(35, 0, 18),)),
    Item("Uptime", "How long this dashboard has been open.", None, spots=(Spot(56, 0, 6),)),
))

STATUS_RIGHT = Crop(78, 0, 44, 1, (
    Item("Verdict", "The state of the whole stack, and the first thing to read.",
         "concepts/severity.md#the-overall-verdict", "The overall verdict", (Spot(81, 0, 9),)),
    Item("Problem counts", "How many topics are stale (`■`), in error (`✗`) or in warning (`▲`). Nodes are not counted.",
         "concepts/severity.md#levels-decide-severity", "Severity", (Spot(91, 0, 5),)),
    Item("Weighted health", "100% when every topic is OK, falling as topics degrade. Shown when `severity_mode` is `both` or `weighted`.",
         "concepts/severity.md#the-overall-verdict", "The overall verdict", (Spot(98, 0, 23),)),
))

PLUGIN_PANEL = Crop(0, 1, 33, 20, (
    Item("Category",
         "Topics grouped by what they are. Click a row above to open its page: "
         + ", ".join(f"[{name}](../{target})" for target, name in _CATEGORIES) + ".",
         "plugins/index.md", "Plugins",
         tuple(Spot(1, 2 + index, 18, target=target, badge="left" if index == 0 else None)
               for index, (target, _name) in enumerate(_CATEGORIES))),
    Item("Severity breakdown", "How many of the category's topics are at each severity, worst first.",
         "concepts/severity.md", "Severity", (Spot(20, 2, 12, 11, badge="right"),)),
    Item("Sort marker", "`▼` sorts worst first. Press ++s++ to sort by name, and the marker moves to the name column as `▲`.",
         "tui/keys.md", "Keys", (Spot(27, 1, 1, badge="right"),)),
    Item("Needs attention", "Up to five active codes across the whole stack, worst first.",
         "concepts/faults.md", "Faults", (Spot(1, 14, 15, badge="right"),)),
    Item("Code severity", "How serious this code is on its own. It can be lower than its topic's severity.",
         "concepts/severity.md#overriding-one-code", "Overriding one code", (Spot(1, 15, 1, 5, badge="left"),)),
    Item("Code", "What is wrong. Every code has an entry with its likely causes and what to do.",
         "reference/codes.md", "All codes", (Spot(3, 15, 8, 5, badge="bottom-right"),)),
    Item("Where", "The topic with the code, shortened from the left, or `6 topics` when several share it.",
         "tui/keys.md", "Keys", (Spot(11, 15, 18, 5, badge="right"),)),
    Item("More", "Codes that did not fit. ++n++ still visits every one of their topics.",
         "tui/keys.md", "Keys", (Spot(1, 20, 7, badge="left"),)),
))

TOPIC_PANEL = Crop(33, 1, 89, 2, (
    Item("Topic", "Every topic in the highlighted category, shown by the tier that monitors it.",
         "concepts/tiers.md", "Monitoring tiers", (Spot(34, 2, 29, badge="bottom"),)),
    Item("Status", "The topic's severity, or a label when it is quiet by design.",
         "concepts/liveness.md", "Liveness", (Spot(90, 2, 8, badge="bottom"),)),
    Item("Hz", "The publish rate over the last 20 messages, `-` until there is one.",
         "core-checks/live.md#live-004", "LIVE-004", (Spot(101, 2, 4, badge="bottom"),)),
    Item("Plugin column", "One number chosen by the plugin. Cameras show the frame size here.",
         "plugins/image-stream.md", "Image stream", (Spot(109, 2, 6, badge="bottom"),)),
))

DETAIL_PANEL = Crop(0, 24, 88, 9, (
    Item("Severity", "The highlighted topic's current severity.",
         "concepts/severity.md#levels-decide-severity", "Severity", (Spot(1, 24, 7),)),
    Item("Topic name", "The full name. Press ++y++ to copy it.", "tui/keys.md", "Keys", (Spot(10, 24, 29),)),
    Item("Message type", "The type decides which plugin checks the topic.", "plugins/index.md", "Plugins", (Spot(40, 24, 31),)),
    Item("Tier", "How closely Testudo monitors this topic.", "concepts/tiers.md#full-tier", "Monitoring tiers", (Spot(74, 24, 9),)),
    Item("`stream`", "Image format, size of the last frame and throughput of the frames checked.",
         "plugins/image-stream.md", "Image stream", (Spot(1, 25, 57, badge="right"),)),
    Item("`sizes`", "The last 20 frame sizes, and the newest frame as a share of their average.",
         "plugins/image-stream.md#img-004", "IMG-004", (Spot(1, 26, 58, badge="right"),)),
    Item("`stuck`", "How many checked frames in a row were byte-identical.",
         "plugins/image-stream.md#img-003", "IMG-003", (Spot(1, 27, 8, badge="right"),)),
    Item("`payload`", "Whether the data starts the way its declared format should.",
         "plugins/image-stream.md#img-002", "IMG-002", (Spot(1, 28, 67, badge="right"),)),
    Item("`frame`", "The message's `header.frame_id`.", "plugins/image-stream.md#img-005", "IMG-005",
         (Spot(1, 29, 22, badge="right"),)),
    Item("`stamp lag`", "How far the message's stamp is behind the clock.", "core-checks/stamp.md#stamp-003", "STAMP-003",
         (Spot(1, 30, 18, badge="right"),)),
    Item("Active code", "What is wrong on this topic now, one line per code.",
         "plugins/image-stream.md#img-002", "IMG-002", (Spot(1, 32, 66, badge="right"),)),
))

HINT_BAR = Crop(0, 34, 100, 1, _hint_items())

#: The file each group of figures is written to, under `docs/includes/dashboard/`.
SECTIONS: dict[str, tuple[Crop, ...]] = {
    "status-bar": (STATUS_LEFT, STATUS_RIGHT),
    "plugin-panel": (PLUGIN_PANEL,),
    "topic-panel": (TOPIC_PANEL,),
    "detail-panel": (DETAIL_PANEL,),
    "hint-bar": (HINT_BAR,),
}

#: The whole screen: letter, pane, the page's section, its cells (col, row, width, height), badge position.
OVERVIEW = (
    ("A", "Status Bar", "status-bar", (0, 0, 122, 1), "top"),
    ("B", "Plugin Panel", "plugin-panel", (0, 1, 33, 22), "inside"),
    ("C", "Topic Panel", "topic-panel", (33, 1, 89, 22), "inside"),
    ("D", "Detail Panel", "detail-panel", (0, 23, 122, 11), "inside"),
    ("E", "Hint bar", "hint-bar", (0, 34, 122, 1), "bottom"),
)


def _href(target: str) -> str:
    """The built page's URL for docs path `target`, from the Dashboard page."""
    path, _, anchor = target.partition("#")
    page = path.removesuffix(".md").removesuffix("index").rstrip("/")
    url = f"../{page}/" if page else "../"
    return f"{url}#{anchor}" if anchor else url


def _percent(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".") + "%"


def _box(col: int, row: int, width: int, height: int, crop: Crop) -> str:
    left = (col - crop.col) / crop.width * 100
    top = (row - crop.row) / crop.height * 100
    return (
        f"left:{_percent(left)};top:{_percent(top)};"
        f"width:{_percent(width / crop.width * 100)};height:{_percent(height / crop.height * 100)}"
    )


def _frame(crop: Crop, spots: list[str], alt: str) -> str:
    """The cropped screenshot with `spots` over it."""
    x = 1 + crop.col * CELL_WIDTH
    y = 1 + crop.row * CELL_HEIGHT
    width = crop.width * CELL_WIDTH
    height = crop.height * CELL_HEIGHT
    image = (
        f'<img src="{IMAGE_SRC}" alt="{escape(alt, quote=True)}" style="width:{_percent(IMAGE_WIDTH / width * 100)};'
        f'left:{_percent(-x / width * 100)};top:{_percent(-y / height * 100)}">'
    )
    return "\n".join([
        f'<figure class="tx-map" style="max-width:{round(width * 1.15)}px">',
        f'<div class="tx-map__frame" style="aspect-ratio:{width}/{height}">',
        f'<div class="tx-map__view">{image}</div>',
        *spots,
        "</div>",
        "</figure>",
    ])


def _spot(spot: Spot, crop: Crop, href: str | None, title: str, number: int | str) -> str:
    """A marked area: a link, or a plain mark when no page covers it."""
    badge = f'<b class="tx-spot__badge--{spot.badge}">{number}</b>' if spot.badge else ""
    box = _box(spot.col, spot.row, spot.width, spot.height, crop)
    attributes = f'class="tx-spot" style="{box}" title="{escape(title, quote=True)}"'
    if href is None:
        return f"<span {attributes}>{badge}</span>"
    return f'<a {attributes} href="{href}">{badge}</a>'


def _plain(markdown: str) -> str:
    return markdown.replace("`", "").replace("++", "")


def section_markup(crops: tuple[Crop, ...], first_number: int) -> str:
    """The figures of one section, then the table their numbers refer to."""
    figures = []
    rows = ["| # | On screen | What it tells you | Read more |", "|---|---|---|---|"]
    number = first_number
    for crop in crops:
        spots = []
        for item in crop.items:
            title = f"{_plain(item.name)}: {_plain(item.text.split(': [')[0])}"
            for spot in item.spots:
                target = spot.target or item.target
                spots.append(_spot(spot, crop, _href(target) if target else None, title, number))
            link = f"[{item.link}](../{item.target})" if item.target else "–"
            rows.append(f'| <span class="tx-num">{number}</span> | **{item.name}** | {item.text} | {link} |')
            number += 1
        names = ", ".join(_plain(item.name) for item in crop.items)
        figures.append(_frame(crop, spots, f"Part of the dashboard, with these marked: {names}"))
    return "\n\n".join(["\n".join(figures), "\n".join(rows)]) + "\n"


def overview_markup() -> str:
    """The whole dashboard, each pane linking to its section of the page."""
    whole = Crop(0, 0, COLUMNS, ROWS)
    spots = [
        f'<a class="tx-spot tx-spot--pane" style="{_box(*cells, whole)}" href="#{section}" title="{name}">'
        f'<b class="tx-spot__badge--{badge}">{letter}</b></a>'
        for letter, name, section, cells, badge in OVERVIEW
    ]
    legend = " ".join(
        f'<a class="tx-pane-link" href="#{section}"><b>{letter}</b> {name}</a>' for letter, name, section, _cells, _badge in OVERVIEW
    )
    figure = _frame(whole, spots, "The Testudo dashboard, with its panes marked")
    return f'{figure}\n<p class="tx-pane-links">{legend}</p>\n'


def generated_files(includes: Path) -> dict[Path, str]:
    """Every include of the Dashboard page and its content. Numbers run on across the sections."""
    folder = includes / "dashboard"
    files = {folder / "overview.md": overview_markup()}
    number = 1
    for name, crops in SECTIONS.items():
        files[folder / f"{name}.md"] = section_markup(crops, number)
        number += sum(len(crop.items) for crop in crops)
    return files
