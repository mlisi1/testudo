"""A `StatusPicture` as Detail Panel markup: half blocks, two square pixels per cell."""
from __future__ import annotations

from testudo.plugins.picture import StatusPicture

_HALF_BLOCK = "▀"


def _fitted_size(picture: StatusPicture, width: int, rows: int) -> tuple[int, int]:
    """The largest (columns, pixel rows) keeping the picture's shape inside `width` x `rows` cells."""
    scale = max(picture.width / width, picture.height / (rows * 2))
    columns = max(1, min(width, round(picture.width / scale)))
    pixel_rows = max(2, min(rows * 2, round(picture.height / scale)))
    return columns, pixel_rows - pixel_rows % 2


def _resampled(picture: StatusPicture, columns: int, pixel_rows: int) -> list[list[int]]:
    """The picture at the new size, each pixel the highest index among those it covers."""
    spans = [(x * picture.width // columns, max(x * picture.width // columns + 1, (x + 1) * picture.width // columns)) for x in range(columns)]
    resampled = []
    for y in range(pixel_rows):
        first = y * picture.height // pixel_rows
        last = max(first + 1, (y + 1) * picture.height // pixel_rows)
        sources = [picture.pixels[row * picture.width : (row + 1) * picture.width] for row in range(first, last)]
        resampled.append([max(max(source[start:end]) for source in sources) for start, end in spans])
    return resampled


def picture_lines(picture: StatusPicture, width: int, rows: int) -> list[str]:
    """Markup lines drawing `picture` within `width` x `rows` cells; none if it can't be drawn."""
    if width < 1 or rows < 1 or picture.width < 1 or picture.height < 1:
        return []
    columns, pixel_rows = _fitted_size(picture, width, rows)
    pixels = _resampled(picture, columns, pixel_rows)
    lines = []
    for top, bottom in zip(pixels[::2], pixels[1::2]):
        runs: list[tuple[tuple[int, int], int]] = []
        for pair in zip(top, bottom):
            if runs and runs[-1][0] == pair:
                runs[-1] = (pair, runs[-1][1] + 1)
            else:
                runs.append((pair, 1))
        lines.append(
            "".join(
                f"[{picture.palette[upper]} on {picture.palette[lower]}]{_HALF_BLOCK * count}[/]"
                for (upper, lower), count in runs
            )
        )
    return lines
