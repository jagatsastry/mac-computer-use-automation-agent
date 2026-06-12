#!/usr/bin/env python
"""End-to-end test runner for the automation agent — sandboxed, rerunnable.

Runs real agent tasks (LLM planning + vision grounding + verification)
against a Docker X11 desktop instead of the live macOS desktop. The host
screen, mouse, and keyboard are never touched.

Every scenario is judged by an INDEPENDENT ground-truth check against the
sandbox browser (via Chrome DevTools Protocol) or window state — not by
the agent's own self-reported success.

Usage:
    scripts/sandbox/run_e2e.sh                       # all scenarios
    scripts/sandbox/run_e2e.sh --scenarios search_widgets,contact_form
    scripts/sandbox/run_e2e.sh --runs 2              # stability: run suite twice
    scripts/sandbox/run_e2e.sh --task "Open http://localhost:8000/ and ..."
    scripts/sandbox/run_e2e.sh --list

Watch live: open a VNC viewer at vnc://localhost:15900 (no password).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone  # noqa: UP017 — venv may be Python 3.9
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

IMAGE = "agent-sandbox:latest"
CONTAINER = "agent-sandbox"
RUN_ARGS = [
    "-p", "15900:5900",   # VNC
    "-p", "19222:9223",   # CDP
    "-p", "18000:8000",   # test site (host-side debugging)
]
SITE = "http://localhost:8000"  # as seen from inside the sandbox

# Prefixed to every scenario prompt. Without it the planner assumes macOS
# affordances — e.g. preconditions like "the macOS desktop is visible"
# (conclusively denied on the Linux desktop) or Spotlight (cmd+space)
# replans that cannot work here.
ENV_NOTE = (
    "(Environment note: minimal test desktop — no Spotlight, no Dock, no "
    "menu bar. Launch apps with activate_app and open pages with open_url.) "
)


def sh(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


# ---------------------------------------------------------------------------
# Container lifecycle
# ---------------------------------------------------------------------------


def ensure_container(rebuild: bool = False) -> None:
    if sh(["docker", "info"]).returncode != 0:
        sys.exit("Docker daemon is not running. Start Docker Desktop and retry.")

    have_image = sh(["docker", "image", "inspect", IMAGE]).returncode == 0
    if rebuild or not have_image:
        print(f"[sandbox] building image {IMAGE} ...")
        build = subprocess.run(
            ["docker", "build", "-t", IMAGE, str(REPO_ROOT / "scripts/sandbox/image")]
        )
        if build.returncode != 0:
            sys.exit("Image build failed")

    running = (
        sh(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER]).stdout.strip()
        == "true"
    )
    if rebuild and running:
        sh(["docker", "rm", "-f", CONTAINER])
        running = False
    if not running:
        sh(["docker", "rm", "-f", CONTAINER])
        print(f"[sandbox] starting container {CONTAINER} ...")
        result = sh(["docker", "run", "-d", "--name", CONTAINER, *RUN_ARGS, IMAGE])
        if result.returncode != 0:
            sys.exit(f"Container start failed: {result.stderr}")

    for _ in range(30):
        if sh(["docker", "exec", CONTAINER, "xdpyinfo", "-display", ":99"]).returncode == 0:
            return
        time.sleep(0.5)
    sys.exit("Sandbox X display did not come up")


def reset_sandbox_apps() -> None:
    """Fresh browser profile + no stray app windows between scenarios.

    Comm-name pkill only — `pkill -f chromium` would match this very
    `sh -c` command line and kill the reset script itself before the
    remaining cleanup ran (it did; galculator survived five resets).
    """
    sh(["docker", "exec", CONTAINER, "sh", "-c",
        "pkill chromium; pkill galculator; pkill mousepad; pkill xterm; "
        "rm -rf /tmp/chromium-profile; true"])
    for _ in range(10):
        leftovers = sh(["docker", "exec", CONTAINER, "sh", "-c",
                        "pgrep chromium; pgrep galculator; pgrep mousepad; pgrep xterm; true"])
        if not leftovers.stdout.strip():
            break
        time.sleep(0.5)
    time.sleep(0.5)


# ---------------------------------------------------------------------------
# Scenarios with independent ground truth
# ---------------------------------------------------------------------------


@dataclass
class Scenario:
    name: str
    prompt: str
    ground_truth: Callable[[object, object], tuple[bool, str]]
    # Stress scenarios exercise known-hard agent behaviors that are not yet
    # reliable (iterative "scroll to the very bottom", exact-once clicks on
    # controls the planner sometimes writes unverifiable postconditions for).
    # They are excluded from the default suite (kept green + rerunnable) and
    # run only with --stress, so the gaps stay tracked without masking
    # regressions in the stable set.
    stress: bool = False


def _page(cdp) -> tuple[str, str]:
    """Return (url, h1 heading) of the active sandbox page."""
    page = cdp.active_page() or {}
    url = page.get("url", "")
    heading = cdp.evaluate(
        "(document.querySelector('h1') ? document.querySelector('h1').textContent : '')"
    ) or ""
    return url, heading


def gt_portal(actuator, cdp):
    url, heading = _page(cdp)
    ok = url.rstrip("/") in (f"{SITE}", f"{SITE}/index.html") and "Sandbox Test Portal" in heading
    return ok, f"url={url} heading={heading!r}"


def gt_search(actuator, cdp):
    url, heading = _page(cdp)
    ok = (
        "results.html" in url
        and "q=blue+widgets" in url.replace("%20", "+")
        and "Search Results" in heading
    )
    return ok, f"url={url} heading={heading!r}"


def gt_form(actuator, cdp):
    url, heading = _page(cdp)
    ok = (
        "thanks.html" in url
        and "name=Alice" in url
        and "alice" in url.lower()
        and "Thank You" in heading
    )
    return ok, f"url={url} heading={heading!r}"


def gt_calculator(actuator, cdp):
    state = actuator.get_state()
    ok = "calculator" in state.get("app_name", "").lower()
    return ok, f"app_name={state.get('app_name')!r}"


def gt_scrolled(actuator, cdp):
    url, _ = _page(cdp)
    scroll_y = cdp.scroll_position("y")
    ok = "article.html" in url and scroll_y is not None and scroll_y > 50
    return ok, f"url={url} scrollY={scroll_y}"


def _cart_items(cdp):
    raw = cdp.evaluate("localStorage.getItem('cart') || '[]'")
    try:
        items = json.loads(raw) if isinstance(raw, str) else []
    except json.JSONDecodeError:
        items = []
    return items


def gt_cart(expected_items, require_cart_page):
    """Cart contents must match EXACTLY — catches wrong-button clicks."""

    def check(actuator, cdp):
        url, _ = _page(cdp)
        items = _cart_items(cdp)
        ok = sorted(items) == sorted(expected_items)
        if require_cart_page:
            ok = ok and "cart.html" in url
        return ok, f"cart={items} url={url}"

    return check


def gt_tile_navigation(actuator, cdp):
    """Reached shop.html AND document.referrer proves link-click navigation
    (open_url would leave referrer empty)."""
    url, heading = _page(cdp)
    referrer = cdp.evaluate("document.referrer") or ""
    ok = "shop.html" in url and "Mini Shop" in heading and referrer.rstrip("/").endswith(
        "localhost:8000"
    )
    return ok, f"url={url} referrer={referrer!r}"


def gt_scrolled_to_bottom(actuator, cdp):
    url, _ = _page(cdp)
    at_bottom = cdp.evaluate(
        "window.scrollY >= document.documentElement.scrollHeight - window.innerHeight - 100"
    )
    scroll_y = cdp.scroll_position("y")
    ok = "article.html" in url and at_bottom is True
    return ok, f"url={url} scrollY={scroll_y} at_bottom={at_bottom}"


SCENARIOS = [
    Scenario(
        name="open_portal",
        prompt=f"Open {SITE}/ in the browser",
        ground_truth=gt_portal,
    ),
    Scenario(
        name="search_widgets",
        prompt=(
            f"Open {SITE}/search.html in the browser, type 'blue widgets' into the "
            "search box, and click the Search button to run the search"
        ),
        ground_truth=gt_search,
    ),
    Scenario(
        name="contact_form",
        prompt=(
            f"Open {SITE}/form.html in the browser, type 'Alice' into the 'Your Name' "
            "field, type 'alice@example.com' into the 'Email Address' field, then "
            "click the 'Send Message' button"
        ),
        ground_truth=gt_form,
    ),
    Scenario(
        name="calculator_app",
        prompt="Open the Calculator app",
        ground_truth=gt_calculator,
    ),
    Scenario(
        name="scroll_article",
        prompt=f"Open {SITE}/article.html in the browser and scroll down the page",
        ground_truth=gt_scrolled,
    ),
    Scenario(
        name="shop_add_notebook",
        prompt=(
            f"Open {SITE}/shop.html in the browser, click the 'Add to Cart' button "
            "for the Blue Notebook, then click the 'View Cart' link"
        ),
        ground_truth=gt_cart(["Blue Notebook"], require_cart_page=True),
        # Grounding the right row is fixed (positional always-validate), but
        # exact-once still flakes on the duplicate-add bug (see shop_green_lamp).
        stress=True,
    ),
    Scenario(
        name="shop_green_lamp",
        prompt=(
            f"Open {SITE}/shop.html in the browser and click the 'Add to Cart' "
            "button in the Green Lamp row"
        ),
        ground_truth=gt_cart(["Green Lamp"], require_cart_page=False),
        # Exact-once: the planner sometimes writes an unverifiable click
        # postcondition ("the button is no longer visible"), the vision verify
        # denies it, and the retry re-clicks → duplicate adds. Tracked gap.
        stress=True,
    ),
    Scenario(
        name="portal_tile_nav",
        prompt=(
            f"Open {SITE}/ in the browser, then click the 'Mini Shop' tile to "
            "navigate to the shop page"
        ),
        ground_truth=gt_tile_navigation,
    ),
    Scenario(
        name="scroll_to_bottom",
        prompt=(
            f"Open {SITE}/article.html in the browser and scroll all the way down "
            "to the bottom of the page until the 'END OF ARTICLE' marker is visible"
        ),
        ground_truth=gt_scrolled_to_bottom,
        # Iterative scroll-until-condition: needs the planner to keep scrolling
        # until the bottom is reached, not a fixed handful of scroll steps.
        # Tracked gap.
        stress=True,
    ),
]


# ---------------------------------------------------------------------------
# Agent assembly (mirrors __main__.run_agent, sandbox-flavored)
# ---------------------------------------------------------------------------


class AutoApproveConfirmationHandler:
    """Non-interactive runs: approve destructive-classified steps and log them."""

    def __init__(self):
        self.approved: list[str] = []

    async def confirm(self, step) -> bool:
        self.approved.append(f"{step.action}:{json.dumps(step.params, default=str)[:120]}")
        print(f"    [auto-approve] {step.action} {step.params}")
        return True


def build_agent(scenario_dir: Path, overlay: bool = False):
    from automation_agent.actuator import create_actuator
    from automation_agent.config import AgentConfig
    from automation_agent.orchestrator import AutomationAgent
    from automation_agent.orchestrator.grounding_router import GroundingRouter
    from automation_agent.orchestrator.screenshot_diff import ScreenshotDiffVerifier
    from automation_agent.planner import ActionPlannerImpl
    from automation_agent.sandbox.capture import SandboxScreenCapture
    from automation_agent.skills import SkillRegistryImpl
    from automation_agent.vision import ScreenCoordinatorImpl

    config = AgentConfig(
        actuator_backend="sandbox",
        use_accessibility=False,
        status_ui="overlay" if overlay else "off",
        save_step_screenshots=True,
        log_dir=scenario_dir,
        event_log_dir=scenario_dir / "runs",
        max_iterations=16,
    )

    planner = ActionPlannerImpl(config)
    skill_registry = SkillRegistryImpl(config=config)
    capture = SandboxScreenCapture(
        container=config.sandbox_container,
        target_resolution=config.screenshot_resolution,
    )
    coordinator = ScreenCoordinatorImpl(config, capture=capture)
    actuator = create_actuator(config)

    grounding_router = None
    try:
        grounding_router = GroundingRouter(
            accessibility=None, vision_coordinator=coordinator, config=config
        )
    except Exception:
        pass

    screenshot_diff = None
    try:
        screenshot_diff = ScreenshotDiffVerifier(capturer=coordinator.capture)
    except Exception:
        pass

    confirmation = AutoApproveConfirmationHandler()
    agent = AutomationAgent(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
        screenshot_diff=screenshot_diff,
        context_monitor=None,
        grounding_router=grounding_router,
        confirmation_handler=confirmation,
    )
    return agent, actuator, config


def run_scenario(scenario: Scenario, run_root: Path, timeout_s: int, overlay: bool = False) -> dict:
    from automation_agent.sandbox.cdp import CdpClient

    scenario_dir = run_root / scenario.name
    scenario_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== [{scenario.name}] {scenario.prompt}")
    reset_sandbox_apps()

    agent, actuator, config = build_agent(scenario_dir, overlay=overlay)
    status_overlay = None
    if overlay:
        try:
            from automation_agent.status import StatusOverlayController

            status_overlay = StatusOverlayController(
                config=config, events_file=agent.logger.events_file
            )
            status_overlay.start()
        except Exception:
            status_overlay = None
    cdp = CdpClient(port=config.sandbox_cdp_port)

    started = time.monotonic()
    error = None
    exec_result = None
    try:
        exec_result = asyncio.run(
            asyncio.wait_for(agent.execute(ENV_NOTE + scenario.prompt), timeout=timeout_s)
        )
    except TimeoutError:
        error = f"scenario timed out after {timeout_s}s"
    except Exception as exc:  # noqa: BLE001 — report, don't crash the suite
        error = f"{type(exc).__name__}: {exc}"
    duration = round(time.monotonic() - started, 1)

    try:
        gt_ok, gt_detail = scenario.ground_truth(actuator, cdp)
    except Exception as exc:  # noqa: BLE001
        gt_ok, gt_detail = False, f"ground truth check crashed: {exc}"

    # Final screenshot for the report, regardless of outcome
    try:
        from automation_agent.sandbox.capture import SandboxScreenCapture

        shot = SandboxScreenCapture(container=CONTAINER).capture()
        (scenario_dir / "final_state.jpg").write_bytes(shot)
    except Exception:
        pass

    agent_success = bool(exec_result.success) if exec_result else False
    record = {
        "scenario": scenario.name,
        "prompt": scenario.prompt,
        "ground_truth_pass": gt_ok,
        "ground_truth_detail": gt_detail,
        "agent_reported_success": agent_success,
        "agent_message": (exec_result.message if exec_result else error) or "",
        "steps_executed": len(exec_result.steps) if exec_result else 0,
        "iterations": exec_result.iterations if exec_result else 0,
        "duration_s": duration,
        "error": error,
        "artifacts_dir": str(scenario_dir),
    }
    verdict = "PASS" if gt_ok else "FAIL"
    mismatch = ""
    if gt_ok != agent_success:
        mismatch = f"  (agent self-report: {agent_success} — mismatch!)"
    print(f"=== [{scenario.name}] {verdict} in {duration}s — {gt_detail}{mismatch}")
    return record


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", help="comma-separated scenario names (default: all)")
    parser.add_argument("--task", help="run one ad-hoc prompt instead of scenarios")
    parser.add_argument("--runs", type=int, default=1, help="repeat the suite N times")
    parser.add_argument("--timeout", type=int, default=420, help="per-scenario timeout (s)")
    parser.add_argument("--rebuild", action="store_true", help="rebuild image + container")
    parser.add_argument("--overlay", action="store_true",
                        help="show the on-host status overlay during runs (off by default: sandbox runs aim for zero host-screen footprint)")
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    parser.add_argument("--stress", action="store_true",
                        help="include stress scenarios (known-hard, not yet reliable)")
    args = parser.parse_args()

    if args.list:
        for s in SCENARIOS:
            tag = " [stress]" if s.stress else ""
            print(f"{s.name:18s}{tag} {s.prompt}")
        return 0

    # Resolve .env, logs/ etc. against the repo root regardless of caller cwd
    import os

    os.chdir(REPO_ROOT)

    ensure_container(rebuild=args.rebuild)

    run_root = REPO_ROOT / "logs" / "sandbox_e2e" / datetime.now(timezone.utc).strftime(  # noqa: UP017
        "%Y%m%d_%H%M%S"
    )
    run_root.mkdir(parents=True, exist_ok=True)
    print(f"[sandbox] artifacts: {run_root}")
    print("[sandbox] watch live: VNC to localhost:15900 (no password)")

    if args.task:
        selected = [
            Scenario(
                name="adhoc",
                prompt=args.task,
                ground_truth=lambda a, c: (True, "ad-hoc task — no ground truth defined"),
            )
        ]
    else:
        by_name = {s.name: s for s in SCENARIOS}
        if args.scenarios:
            missing = [n for n in args.scenarios.split(",") if n not in by_name]
            if missing:
                sys.exit(f"Unknown scenarios: {missing}. Use --list.")
            selected = [by_name[n] for n in args.scenarios.split(",")]
        else:
            # Default suite excludes stress scenarios so it stays green and
            # rerunnable; --stress opts into the known-hard ones.
            selected = [s for s in SCENARIOS if args.stress or not s.stress]

    records = []
    for round_idx in range(1, args.runs + 1):
        round_root = run_root if args.runs == 1 else run_root / f"round{round_idx}"
        for scenario in selected:
            records.append(run_scenario(scenario, round_root, args.timeout, overlay=args.overlay))

    (run_root / "results.json").write_text(json.dumps(records, indent=2))

    passed = sum(1 for r in records if r["ground_truth_pass"])
    print(f"\n{'='*64}")
    print(f"SANDBOX E2E: {passed}/{len(records)} scenarios passed (ground truth)")
    for r in records:
        flag = "PASS" if r["ground_truth_pass"] else "FAIL"
        print(f"  [{flag}] {r['scenario']:18s} {r['duration_s']:6.1f}s  {r['ground_truth_detail'][:80]}")
    print(f"Results: {run_root / 'results.json'}")
    return 0 if passed == len(records) else 1


if __name__ == "__main__":
    sys.exit(main())
