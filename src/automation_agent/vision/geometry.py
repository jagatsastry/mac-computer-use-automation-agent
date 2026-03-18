"""Geometry helpers for mapping between logical screen space and image space."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class ContentRect:
    """The rendered screen rectangle inside a screenshot/image."""

    left: float
    top: float
    width: float
    height: float
    scale: float


def fit_screen_into_image(
    screen_size: Tuple[int, int],
    image_size: Tuple[int, int],
) -> ContentRect:
    """Return the centered content rect when preserving aspect ratio."""

    screen_width = max(int(screen_size[0]), 1)
    screen_height = max(int(screen_size[1]), 1)
    image_width = max(int(image_size[0]), 1)
    image_height = max(int(image_size[1]), 1)

    scale = min(image_width / screen_width, image_height / screen_height)
    content_width = screen_width * scale
    content_height = screen_height * scale
    left = (image_width - content_width) / 2.0
    top = (image_height - content_height) / 2.0

    return ContentRect(
        left=left,
        top=top,
        width=content_width,
        height=content_height,
        scale=scale,
    )


def screen_to_image_coords(
    screen_x: int,
    screen_y: int,
    screen_size: Tuple[int, int],
    image_size: Tuple[int, int],
) -> Tuple[int, int]:
    """Project logical screen coordinates into a letterboxed image."""

    screen_width = max(int(screen_size[0]), 1)
    screen_height = max(int(screen_size[1]), 1)
    image_width = max(int(image_size[0]), 1)
    image_height = max(int(image_size[1]), 1)
    rect = fit_screen_into_image(screen_size, image_size)

    clamped_screen_x = max(0, min(int(screen_x), screen_width - 1))
    clamped_screen_y = max(0, min(int(screen_y), screen_height - 1))

    image_x = round(rect.left + clamped_screen_x * rect.scale)
    image_y = round(rect.top + clamped_screen_y * rect.scale)
    return (
        max(0, min(image_x, image_width - 1)),
        max(0, min(image_y, image_height - 1)),
    )


def image_to_screen_coords(
    image_x: int,
    image_y: int,
    screen_size: Tuple[int, int],
    image_size: Tuple[int, int],
) -> Tuple[int, int]:
    """Map image coordinates back to logical screen coordinates."""

    screen_width = max(int(screen_size[0]), 1)
    screen_height = max(int(screen_size[1]), 1)
    image_width = max(int(image_size[0]), 1)
    image_height = max(int(image_size[1]), 1)
    rect = fit_screen_into_image(screen_size, image_size)

    clamped_image_x = max(rect.left, min(float(image_x), rect.left + rect.width))
    clamped_image_y = max(rect.top, min(float(image_y), rect.top + rect.height))

    screen_x = round((clamped_image_x - rect.left) / rect.scale)
    screen_y = round((clamped_image_y - rect.top) / rect.scale)
    return (
        max(0, min(screen_x, screen_width - 1)),
        max(0, min(screen_y, screen_height - 1)),
    )
