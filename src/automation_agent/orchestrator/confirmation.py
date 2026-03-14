"""Confirmation handlers for destructive action gates."""

import asyncio
import re
from typing import Any

from automation_agent.shared_models import ActionStep


def _sanitize_for_display(value: Any) -> str:
    """Strip ANSI escape sequences and control characters from display values.

    Security: LLM-generated params may contain ANSI escape codes or
    Unicode directional overrides that could mislead the user.
    """
    text = str(value)
    # Strip ANSI escape sequences (CSI, OSC, etc.)
    text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)   # CSI sequences
    text = re.sub(r'\x1b\][^\x07]*\x07', '', text)       # OSC sequences
    text = re.sub(r'\x1b[^[]\S', '', text)                # Other escapes
    # Strip Unicode directional overrides and other control chars
    text = re.sub(r'[\u200e\u200f\u202a-\u202e\u2066-\u2069]', '', text)
    # Strip remaining C0/C1 control characters (except newline, tab)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)
    return text


class ConsoleConfirmationHandler:
    """Default: prints to stdout, reads from stdin via asyncio.to_thread()."""

    _DEFAULT_TIMEOUT_S: float = 120.0

    def __init__(self, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        self._timeout_s = timeout_s

    async def confirm(self, step: ActionStep) -> bool:
        """AC-7: Display sanitized action details and block until user confirms."""
        safe_action = _sanitize_for_display(step.action)
        safe_params = _sanitize_for_display(step.params)
        safe_verify = _sanitize_for_display(step.verify)

        print("\n" + "=" * 60)
        print("DESTRUCTIVE ACTION — Confirmation Required")
        print(f"  (auto-deny in {self._timeout_s:.0f}s if no response)")
        print("=" * 60)
        print(f"  Action:      {safe_action}")
        print(f"  Parameters:  {safe_params}")
        print(f"  Postcondition: {safe_verify}")
        print("=" * 60)
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(input, "Execute this action? [y/N]: "),
                timeout=self._timeout_s,
            )
            return response.strip().lower() in ("y", "yes")
        except asyncio.TimeoutError:
            print(
                f"\n  [TIMEOUT] No response after {self._timeout_s:.0f}s"
                " — denying action"
            )
            return False
        except (EOFError, KeyboardInterrupt):
            return False


class AutoDenyConfirmationHandler:
    """For headless/CI: always denies destructive actions."""

    async def confirm(self, step: ActionStep) -> bool:
        return False
