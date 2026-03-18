"""Tests for aspect-ratio-preserving screen/image geometry helpers."""

from automation_agent.vision.geometry import (
    fit_screen_into_image,
    image_to_screen_coords,
    screen_to_image_coords,
)


def test_fit_screen_into_image_letterboxes_16_9_into_4_3():
    rect = fit_screen_into_image((1920, 1080), (1024, 768))
    assert rect.left == 0
    assert rect.top == 96
    assert rect.width == 1024
    assert rect.height == 576


def test_screen_to_image_coords_accounts_for_padding():
    x, y = screen_to_image_coords(0, 0, (1920, 1080), (1024, 768))
    assert x == 0
    assert y == 96


def test_image_to_screen_coords_clamps_padding_to_screen_edge():
    x, y = image_to_screen_coords(0, 0, (1920, 1080), (1024, 768))
    assert x == 0
    assert y == 0
