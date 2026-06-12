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
    # The skill must still warn against clicking the product image/title
    # (rephrased in 4e54910 for explicit "View order details" grounding).
    assert "do NOT click the product image, product title" in content
    assert "NEVER click the product image or product title" in content
