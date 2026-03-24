"""Regression tests for built-in skill templates."""

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_return_amazon_order_skill_separates_typing_from_submission():
    content = (
        _repo_root()
        / "src"
        / "automation_agent"
        / "skills"
        / "library"
        / "return_amazon_order.md"
    ).read_text()

    assert 'The "Search all orders" input field contains "{{item}}"' in content
    assert 'press_key ["return"]' in content
    assert 'verify: Orders filtered to show items matching "{{item}}"' not in content
    assert 'NOT the product image/title link' in content
