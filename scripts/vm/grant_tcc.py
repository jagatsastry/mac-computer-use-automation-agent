#!/usr/bin/env python3
"""Grant TCC permissions inside the test VM (requires SIP disabled).

Inserts Screen Recording, Accessibility, synthetic-input, and Apple Events
grants for the process chain used by SSH-driven automation:
sshd -> shell -> python/osascript/screencapture.

ONLY for disposable test VMs (cirruslabs images ship with SIP disabled).
Never run on a real machine.

The TCC.db schema changes across macOS versions, so columns are
introspected and filled by name rather than hardcoded.
"""

from __future__ import annotations  # macOS system python3 may be 3.9

import os
import sqlite3
import subprocess
import sys
import time

SYSTEM_DB = "/Library/Application Support/com.apple.TCC/TCC.db"
USER_DB = os.path.expanduser("~/Library/Application Support/com.apple.TCC/TCC.db")

CLIENTS = [
    "/usr/sbin/sshd",
    "/usr/libexec/sshd-keygen-wrapper",
    "/bin/bash",
    "/bin/zsh",
    "/usr/bin/osascript",
    "/usr/bin/python3",
    "/usr/sbin/screencapture",
]

# venv python resolves to the real interpreter binary — TCC sees that path
venv_python = os.path.expanduser("~/agent/.venv/bin/python")
if os.path.exists(venv_python):
    CLIENTS.append(os.path.realpath(venv_python))

SYSTEM_SERVICES = [
    "kTCCServiceScreenCapture",
    "kTCCServiceAccessibility",
    "kTCCServicePostEvent",
    "kTCCServiceListenEvent",
]

# (service, indirect_object_identifier) rows for the user DB
USER_SERVICES = [
    ("kTCCServiceAppleEvents", "com.apple.systemevents"),
    ("kTCCServiceAppleEvents", "com.apple.finder"),
    ("kTCCServiceAppleEvents", "com.apple.Safari"),
]


def grant(db_path: str, service: str, client: str, indirect: str | None = None) -> bool:
    try:
        conn = sqlite3.connect(db_path)
        cols = [row[1] for row in conn.execute("PRAGMA table_info(access)")]
        if not cols:
            print(f"  !! no access table in {db_path}")
            return False

        values = {}
        for col in cols:
            if col == "service":
                values[col] = service
            elif col == "client":
                values[col] = client
            elif col == "client_type":
                values[col] = 1  # absolute path
            elif col in ("auth_value", "allowed"):
                values[col] = 2  # allowed
            elif col == "auth_reason":
                values[col] = 4
            elif col == "auth_version":
                values[col] = 1
            elif col == "indirect_object_identifier_type":
                values[col] = 0 if indirect else None
            elif col == "indirect_object_identifier":
                values[col] = indirect or "UNUSED"
            elif col == "flags":
                values[col] = 0
            elif col == "last_modified":
                values[col] = int(time.time())
            elif col in ("prompt_count",):
                values[col] = 1
            else:
                values[col] = None  # csreq, policy_id, pid, boot_uuid, ...

        placeholders = ",".join("?" for _ in cols)
        conn.execute(
            f"INSERT OR REPLACE INTO access ({','.join(cols)}) VALUES ({placeholders})",
            [values[c] for c in cols],
        )
        conn.commit()
        conn.close()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  !! {db_path} {service} {client}: {exc}")
        return False


def main() -> int:
    if os.geteuid() == 0:
        # Running as root: do the system DB, then re-run as the login user
        ok = all(
            grant(SYSTEM_DB, service, client)
            for service in SYSTEM_SERVICES
            for client in CLIENTS
        )
        print(f"system TCC grants: {'ok' if ok else 'PARTIAL/FAILED'}")
        login_user = os.environ.get("SUDO_USER", "admin")
        # sys.executable, not /usr/bin/python3 — the latter is an xcrun shim
        # on CLT-less images
        subprocess.run(["sudo", "-u", login_user, sys.executable, __file__])
        # Reload tccd so grants take effect without a reboot
        subprocess.run(["launchctl", "kickstart", "-k", "system/com.apple.tccd"],
                       capture_output=True)
        return 0 if ok else 1

    ok = all(
        grant(USER_DB, service, client, indirect)
        for service, indirect in USER_SERVICES
        for client in CLIENTS
    )
    print(f"user TCC grants: {'ok' if ok else 'PARTIAL/FAILED'}")
    subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.apple.tccd"],
        capture_output=True,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
