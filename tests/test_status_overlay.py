"""Unit tests for the menu bar and status overlay helpers."""

from unittest.mock import patch

from automation_agent.status import StatusSnapshot
from automation_agent import status_overlay


class _FakeButton:
    def __init__(self):
        self.title = ""

    def setTitle_(self, title):
        self.title = title


class _FakeStatusItem:
    def __init__(self):
        self._button = _FakeButton()
        self.menu = None

    def button(self):
        return self._button

    def setMenu_(self, menu):
        self.menu = menu


class _FakeStatusBar:
    def __init__(self):
        self.item = _FakeStatusItem()
        self.removed = None

    def statusItemWithLength_(self, _length):
        return self.item

    def removeStatusItem_(self, item):
        self.removed = item


class _FakeStatusBarClass:
    instance = None

    @staticmethod
    def systemStatusBar():
        return _FakeStatusBarClass.instance


class _FakeMenu:
    def __init__(self):
        self.items = []

    def addItem_(self, item):
        self.items.append(item)


class _FakeMenuFactory:
    @staticmethod
    def alloc():
        return _FakeMenuFactory()

    def init(self):
        return _FakeMenu()


class _FakeMenuItem:
    def __init__(self, title=""):
        self.title = title
        self.enabled = True
        self.target = None

    @staticmethod
    def alloc():
        return _FakeMenuItem()

    @staticmethod
    def separatorItem():
        return _FakeMenuItem("---")

    def initWithTitle_action_keyEquivalent_(self, title, _action, _key):
        self.title = title
        return self

    def setEnabled_(self, enabled):
        self.enabled = enabled

    def setTarget_(self, target):
        self.target = target

    def setTitle_(self, title):
        self.title = title


def test_menu_bar_item_updates_goal_and_current_step():
    """Menu bar item should retain the latest goal and current step."""
    fake_status_bar = _FakeStatusBar()

    with patch.object(status_overlay, "_run_on_main", lambda fn, wait=False: fn()), patch.object(
        status_overlay, "_make_menu_target", lambda callback: callback
    ), patch.object(
        status_overlay, "NSStatusBar", _FakeStatusBarClass
    ), patch.object(
        status_overlay, "NSMenu", _FakeMenuFactory
    ), patch.object(
        status_overlay, "NSMenuItem", _FakeMenuItem
    ):
        _FakeStatusBarClass.instance = fake_status_bar
        item = status_overlay.StatusMenuBarItem(on_quit=lambda: None)
        item.apply_snapshot(
            StatusSnapshot(
                title="Planning",
                line="[03:55:01] Planning...",
                goal="Return headphones on Amazon",
            )
        )
        item.apply_snapshot(
            StatusSnapshot(
                title="Executing: open_url",
                line="[03:55:02] [step 1] Step 1: open_url",
                step_label="Step 1: open_url",
            )
        )

        assert fake_status_bar.item.button().title.startswith("Agent: ")
        assert item._status_item.title.startswith("Status: ")
        assert "Step 1: open_url" in item._step_item.title
        assert "Return headphones on Amazon" in item._goal_item.title
        assert "Step 1: open_url" in item._detail_item.title

        item.destroy()
        assert fake_status_bar.removed is not None


def test_status_ui_forwards_snapshots_to_window_and_menu_bar():
    """StatusUI should fan out each snapshot to both surfaces."""
    snapshot = StatusSnapshot(title="Planning", line="[03:55:01] Planning...")

    with patch.object(status_overlay, "StatusOverlayWindow") as window_cls, patch.object(
        status_overlay, "StatusMenuBarItem"
    ) as menu_cls:
        window = window_cls.return_value
        menu = menu_cls.return_value
        ui = status_overlay.StatusUI(max_lines=20)

        ui.apply_snapshot(snapshot)
        ui.destroy()

    window.apply_snapshot.assert_called_once_with(snapshot)
    menu.apply_snapshot.assert_called_once_with(snapshot)
    menu.destroy.assert_called_once()
    window.destroy.assert_called_once()


def test_tailer_waits_briefly_after_parent_exit_before_destroying():
    """The tailer should allow a short grace period after parent exit."""

    class _FakeUI:
        def __init__(self):
            self.destroyed = False

        def apply_snapshot(self, snapshot):
            pass

        def destroy(self):
            self.destroyed = True

    ui = _FakeUI()
    tailer = status_overlay.StatusOverlayTailer(
        events_file=__import__("pathlib").Path("/tmp/does-not-exist"),
        parent_pid=123,
        poll_interval=0.25,
        linger_seconds=4.0,
        ui=ui,
    )

    with patch.object(status_overlay, "_pid_is_alive", return_value=False), patch.object(
        status_overlay.time, "monotonic", side_effect=[10.0, 10.1, 14.2]
    ):
        assert tailer._tick() is True
        assert ui.destroyed is False
        assert tailer._parent_exit_deadline == 14.0

        assert tailer._tick() is True
        assert ui.destroyed is False

        assert tailer._tick() is False
        assert ui.destroyed is True
