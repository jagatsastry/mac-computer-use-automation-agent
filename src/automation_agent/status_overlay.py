"""Floating macOS overlay that tails the agent event log in real time."""

import argparse
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSColor,
    NSFloatingWindowLevel,
    NSFont,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSPanel,
    NSScreen,
    NSScrollView,
    NSStatusBar,
    NSTextField,
    NSTextView,
    NSVariableStatusItemLength,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskNonactivatingPanel,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
    NSWindowStyleMaskUtilityWindow,
)
from Foundation import NSObject
from PyObjCTools import AppHelper

from automation_agent.status import StatusSnapshot, load_status_snapshot


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for the status overlay process."""
    parser = argparse.ArgumentParser(description="Floating live status overlay")
    parser.add_argument("--events-file", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--poll-interval", type=float, default=0.25)
    parser.add_argument("--max-lines", type=int, default=200)
    parser.add_argument("--linger-seconds", type=float, default=4.0)
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show detailed LLM responses and reasoning",
    )
    return parser


class _MainThreadRunner(NSObject):
    """Helper to dispatch a callable on the AppKit main thread."""

    def runBlock_(self, fn):
        fn()


def _run_on_main(fn, wait: bool = False) -> None:
    """Schedule fn() on the AppKit main thread via performSelectorOnMainThread."""
    runner = _MainThreadRunner.alloc().init()
    runner.performSelectorOnMainThread_withObject_waitUntilDone_(
        b"runBlock:", fn, wait
    )


def _make_menu_target(callback: Callable[[], None]):
    """Create an Objective-C target object for a menu item callback."""

    class _Target(NSObject):
        def clicked_(self, sender):
            callback()

    return _Target.alloc().init()


class StatusOverlayWindow:
    """Always-on-top, click-through status panel."""

    def __init__(self, max_lines: int, verbose: bool = False) -> None:
        self.max_lines = max_lines
        self._verbose = verbose
        self._destroyed = False
        self._lines: list[str] = []
        self._panel = None
        self._header = None
        self._text_view = None
        self._create_window()

    def _create_window(self) -> None:
        width = 420 if self._verbose else 320
        height = 300 if self._verbose else 200
        x = 40
        y = 40
        screen = NSScreen.mainScreen()
        if screen is not None:
            try:
                frame = screen.visibleFrame()
                x = int(frame.origin.x + frame.size.width - width - 24)
                y = int(frame.origin.y + 24)  # bottom-right to avoid covering browser UI
            except Exception:
                pass

        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskResizable
            | NSWindowStyleMaskUtilityWindow
            | NSWindowStyleMaskNonactivatingPanel
        )
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, width, height),
            style,
            NSBackingStoreBuffered,
            False,
        )
        title = "Automation Agent Status (Verbose)" if self._verbose else "Automation Agent Status"
        panel.setTitle_(title)
        panel.setLevel_(NSFloatingWindowLevel)
        panel.setHidesOnDeactivate_(False)
        panel.setIgnoresMouseEvents_(True)
        panel.setOpaque_(False)
        panel.setAlphaValue_(0.95)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        # Exclude overlay from screencapture so the vision model never sees it
        panel.setSharingType_(0)  # NSWindowSharingNone

        content = panel.contentView()

        header = NSTextField.alloc().initWithFrame_(NSMakeRect(16, height - 42, width - 32, 24))
        header.setEditable_(False)
        header.setSelectable_(False)
        header.setBezeled_(False)
        header.setDrawsBackground_(False)
        header.setStringValue_("Starting…")
        header.setFont_(NSFont.boldSystemFontOfSize_(14))
        header.setTextColor_(NSColor.labelColor())
        content.addSubview_(header)

        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(12, 12, width - 24, height - 58))
        scroll.setHasVerticalScroller_(True)
        scroll.setAutoresizingMask_(18)

        text_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, width - 24, height - 58))
        text_view.setEditable_(False)
        text_view.setSelectable_(False)
        font_size = 10 if self._verbose else 12
        text_view.setFont_(NSFont.monospacedSystemFontOfSize_weight_(font_size, 0.0))
        text_view.setTextColor_(NSColor.labelColor())
        text_view.setBackgroundColor_(NSColor.windowBackgroundColor())
        text_view.setAutoresizingMask_(18)

        scroll.setDocumentView_(text_view)
        content.addSubview_(scroll)
        panel.orderFrontRegardless()

        self._panel = panel
        self._header = header
        self._text_view = text_view

    def apply_snapshot(self, snapshot: StatusSnapshot) -> None:
        """Apply a formatted status update to the window."""
        if self._destroyed:
            return

        def _do() -> None:
            if self._destroyed or self._header is None or self._text_view is None:
                return
            self._header.setStringValue_(snapshot.title)
            self._lines.append(snapshot.line)
            if len(self._lines) > self.max_lines:
                self._lines = self._lines[-self.max_lines:]
            text = "\n".join(self._lines)
            self._text_view.setString_(text)
            self._text_view.scrollRangeToVisible_((len(text), 0))
            self._panel.orderFrontRegardless()

        _run_on_main(_do)

    def destroy(self) -> None:
        """Close the panel and tear down the process."""
        if self._destroyed:
            return
        self._destroyed = True

        def _do() -> None:
            try:
                if self._panel is not None:
                    self._panel.close()
            finally:
                NSApplication.sharedApplication().terminate_(None)

        _run_on_main(_do)


class StatusMenuBarItem:
    """Persistent menu bar status for the running agent."""

    def __init__(self, on_quit: Callable[[], None]) -> None:
        self._goal = ""
        self._step_label = ""
        self._item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self._item.button().setTitle_("Agent: Starting")
        self._menu = NSMenu.alloc().init()
        self._status_item = self._disabled_item("Status: Starting")
        self._step_item = self._disabled_item("Current step: —")
        self._goal_item = self._disabled_item("Goal: —")
        self._detail_item = self._disabled_item("Detail: waiting for events")
        self._menu.addItem_(self._status_item)
        self._menu.addItem_(self._step_item)
        self._menu.addItem_(self._goal_item)
        self._menu.addItem_(self._detail_item)
        self._menu.addItem_(NSMenuItem.separatorItem())
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit Status UI",
            b"clicked:",
            "",
        )
        self._target = _make_menu_target(on_quit)
        quit_item.setTarget_(self._target)
        self._menu.addItem_(quit_item)
        self._item.setMenu_(self._menu)

    @staticmethod
    def _disabled_item(title: str):
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
        item.setEnabled_(False)
        return item

    def apply_snapshot(self, snapshot: StatusSnapshot) -> None:
        """Update the menu bar title and menu items."""

        def _do() -> None:
            if snapshot.goal:
                self._goal = snapshot.goal
            if snapshot.step_label:
                self._step_label = snapshot.step_label
            self._item.button().setTitle_(f"Agent: {self._truncate(snapshot.title, 18)}")
            self._status_item.setTitle_(f"Status: {self._truncate(snapshot.title, 60)}")
            self._step_item.setTitle_(
                f"Current step: {self._truncate(self._step_label or '—', 60)}"
            )
            self._goal_item.setTitle_(f"Goal: {self._truncate(self._goal or '—', 60)}")
            self._detail_item.setTitle_(f"Detail: {self._truncate(snapshot.line, 80)}")

        _run_on_main(_do)

    def destroy(self) -> None:
        """Remove the menu bar item."""

        def _do() -> None:
            if self._item is not None:
                NSStatusBar.systemStatusBar().removeStatusItem_(self._item)
                self._item = None

        _run_on_main(_do)

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"


class StatusUI:
    """Owns both the floating overlay and the menu bar item."""

    def __init__(self, max_lines: int, verbose: bool = False) -> None:
        self._destroyed = False
        self.window = StatusOverlayWindow(max_lines=max_lines, verbose=verbose)
        self.menu_bar = StatusMenuBarItem(on_quit=self.destroy)

    def apply_snapshot(self, snapshot: StatusSnapshot) -> None:
        """Fan out one status update to all active UI surfaces."""
        if self._destroyed:
            return
        self.window.apply_snapshot(snapshot)
        self.menu_bar.apply_snapshot(snapshot)

    def destroy(self) -> None:
        """Tear down all UI surfaces and terminate the app."""
        if self._destroyed:
            return
        self._destroyed = True
        self.menu_bar.destroy()
        self.window.destroy()


class StatusOverlayTailer:
    """Background file tailer that feeds the overlay UI."""

    def __init__(
        self,
        events_file: Path,
        parent_pid: int,
        poll_interval: float,
        linger_seconds: float,
        ui: StatusUI,
        verbose: bool = False,
    ) -> None:
        self.events_file = events_file
        self.parent_pid = parent_pid
        self.poll_interval = poll_interval
        self.linger_seconds = linger_seconds
        self.ui = ui
        self.verbose = verbose
        self._offset = 0
        self._deadline: Optional[float] = None
        self._parent_exit_deadline: Optional[float] = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        """Start the background polling thread."""
        self._thread.start()

    def _run(self) -> None:
        while True:
            if not self._tick():
                return
            time.sleep(self.poll_interval)

    def _tick(self) -> bool:
        """Poll once and decide whether the UI should remain alive."""
        self._poll_once()
        now = time.monotonic()

        if self._deadline is not None:
            if now >= self._deadline:
                self.ui.destroy()
                return False
            return True

        if self.parent_pid and not _pid_is_alive(self.parent_pid):
            if self._parent_exit_deadline is None:
                # Give the logger time to flush terminal events after the parent exits.
                self._parent_exit_deadline = now + max(
                    self.linger_seconds,
                    self.poll_interval * 4,
                    1.0,
                )
                return True
            if now >= self._parent_exit_deadline:
                self.ui.destroy()
                return False

        return True

    def _poll_once(self) -> None:
        if not self.events_file.exists():
            return

        with self.events_file.open("r", encoding="utf-8") as handle:
            handle.seek(self._offset)
            while True:
                line = handle.readline()
                if not line:
                    break
                self._offset = handle.tell()
                snapshot = load_status_snapshot(line, verbose=self.verbose)
                if snapshot is None:
                    continue
                self.ui.apply_snapshot(snapshot)
                if snapshot.terminal:
                    self._deadline = time.monotonic() + self.linger_seconds
                    self._parent_exit_deadline = None


def _pid_is_alive(pid: int) -> bool:
    """Return True when the parent PID still exists."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def main(argv: Optional[list[str]] = None) -> None:
    """Run the floating overlay until the parent process exits or the task ends."""
    args = build_parser().parse_args(argv)
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    ui = StatusUI(max_lines=args.max_lines, verbose=args.verbose)
    tailer = StatusOverlayTailer(
        events_file=args.events_file,
        parent_pid=args.parent_pid,
        poll_interval=args.poll_interval,
        linger_seconds=args.linger_seconds,
        ui=ui,
        verbose=args.verbose,
    )
    tailer.start()
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
