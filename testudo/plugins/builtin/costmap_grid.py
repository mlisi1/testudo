"""What an occupancy grid holds: shares of its cells and a small picture of it. Pure numpy."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from testudo.plugins.picture import StatusPicture

#: Cell classes, in the order shrinking the picture favors: the last one wins.
_UNKNOWN, _FREE, _LOW, _HIGH, _LETHAL = range(5)
PALETTE: tuple[str, ...] = ("#1c1f24", "#4b5563", "#b58900", "#d9480f", "#ff3b3b")
CLASS_NAMES: tuple[str, ...] = ("unknown", "free", "low cost", "high cost", "lethal")

#: OccupancyGrid values: -1 unknown, 0 free, 100 lethal; Nav2 scales its costs into 1..99.
_HIGH_COST_FROM = 50
_LETHAL_VALUE = 100

#: The picture is kept at most this large: enough for any pane, small enough to keep per status.
MAX_PICTURE_WIDTH = 240
MAX_PICTURE_HEIGHT = 120


def _class_table() -> np.ndarray:
    """Cell class for every int8 value, indexed by that value's unsigned byte."""
    table = np.full(256, _LETHAL, dtype=np.uint8)  # anything outside -1..100 is no free space
    table[0] = _FREE
    table[1:_HIGH_COST_FROM] = _LOW
    table[_HIGH_COST_FROM:_LETHAL_VALUE] = _HIGH
    table[255] = _UNKNOWN
    return table


_CLASSES = _class_table()


@dataclass(frozen=True)
class GridAnalysis:
    """One moment of a grid: lethal share of its known cells, unknown share of all cells, its picture."""

    lethal_ratio: float
    unknown_ratio: float
    picture: StatusPicture


def _shrunk(classes: np.ndarray) -> np.ndarray:
    """`classes` within the picture's limits, each block keeping its highest class so walls survive."""
    height, width = classes.shape
    factor = max(1, int(np.ceil(max(width / MAX_PICTURE_WIDTH, height / MAX_PICTURE_HEIGHT))))
    if factor == 1:
        return classes
    rows = np.maximum.reduceat(classes, np.arange(0, height, factor), axis=0)
    return np.maximum.reduceat(rows, np.arange(0, width, factor), axis=1)


def analyse(cells: np.ndarray) -> GridAnalysis:
    """Analyse `cells` (int8, row 0 at the grid's origin); the picture has +y up."""
    classes = _CLASSES[cells.astype(np.uint8)]
    counts = np.bincount(classes.ravel(), minlength=len(PALETTE))
    known = int(cells.size - counts[_UNKNOWN])
    shrunk = _shrunk(classes)[::-1]
    picture = StatusPicture(shrunk.shape[1], shrunk.shape[0], shrunk.tobytes(), PALETTE)
    return GridAnalysis(
        lethal_ratio=int(counts[_LETHAL]) / known if known else 0.0,
        unknown_ratio=int(counts[_UNKNOWN]) / cells.size,
        picture=picture,
    )
