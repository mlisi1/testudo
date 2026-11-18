"""A small picture a plugin can attach to its status, for the Detail Panel to draw."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StatusPicture:
    """`width` x `height` palette indices, top row first, one byte each.

    Immutable, so a status keeps showing what was true when it was made.
    A higher index wins when the picture is shrunk to fit the pane: order
    `palette` (hex colors) so that what must not vanish comes last.
    """

    width: int
    height: int
    pixels: bytes
    palette: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.pixels) != self.width * self.height:
            raise ValueError(f"{self.width}x{self.height} picture needs {self.width * self.height} pixels, got {len(self.pixels)}")
