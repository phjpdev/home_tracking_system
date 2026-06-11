"""Plan-pixel geometry helpers for privacy-zone thermal positions."""

from __future__ import annotations


def point_in_polygon(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    if len(poly) < 3:
        return False
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
        ):
            inside = not inside
        j = i
    return inside


def clip_point_to_polygon(
    x: float,
    y: float,
    poly: list[tuple[float, float]],
) -> tuple[float, float] | None:
    """Return (x, y) if inside polygon, else None."""
    if point_in_polygon(x, y, poly):
        return x, y
    return None
