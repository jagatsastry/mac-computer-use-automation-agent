#!/usr/bin/env python3
"""Dump everything the accessibility layer can see for the frontmost app.

Captures both get_state() (what tier-1 verification sees) and
get_accessibility_elements() (what element finding sees), plus
additional AX properties we DON'T currently collect but could.

Usage:
    python scripts/debuggers/dump_accessibility.py
    python scripts/debuggers/dump_accessibility.py --app Safari
    python scripts/debuggers/dump_accessibility.py --out /tmp/ax_dump.json
    python scripts/debuggers/dump_accessibility.py --deep  # walk deeper + more properties
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime


def get_state(app_name=""):
    """Mirror of AppleScriptActuator.get_state() — what tier-1 verification sees.

    Args:
        app_name: Target app. Empty string means frontmost app.
    """
    if app_name:
        target = f'application process "{app_name}"'
    else:
        target = "first application process whose frontmost is true"
    script = f'''
tell application "System Events"
    set frontApp to name of {target}
    set frontBundle to bundle identifier of {target}
    try
        set winTitle to name of front window of {target}
    on error
        set winTitle to ""
    end try
    try
        set winPos to position of front window of {target}
        set winSize to size of front window of {target}
        set winX to item 1 of winPos
        set winY to item 2 of winPos
        set winW to item 1 of winSize
        set winH to item 2 of winSize
    on error
        set winX to 0
        set winY to 0
        set winW to 0
        set winH to 0
    end try
end tell
return frontApp & "|" & frontBundle & "|" & winTitle & "|" & winX & "|" & winY & "|" & winW & "|" & winH
'''
    result = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        return {"error": result.stderr.strip()}
    parts = result.stdout.strip().split("|", 6)
    app_name = parts[0] if len(parts) > 0 else ""
    state = {
        "app_name": app_name,
        "app_bundle": parts[1] if len(parts) > 1 else "",
        "window_title": parts[2] if len(parts) > 2 else "",
        "window_x": int(parts[3]) if len(parts) > 3 and parts[3] else 0,
        "window_y": int(parts[4]) if len(parts) > 4 and parts[4] else 0,
        "window_w": int(parts[5]) if len(parts) > 5 and parts[5] else 0,
        "window_h": int(parts[6]) if len(parts) > 6 and parts[6] else 0,
    }

    # Browser URL (what we DO collect)
    lower = app_name.lower()
    url = ""
    if "safari" in lower:
        r = subprocess.run(
            ["osascript", "-e", 'tell application "Safari" to return URL of front document'],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            url = r.stdout.strip()
    elif "chrome" in lower:
        r = subprocess.run(
            ["osascript", "-e",
             'tell application "Google Chrome" to return URL of active tab of front window'],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            url = r.stdout.strip()
    state["browser_url"] = url
    return state


def get_accessibility_elements(app_name="", max_depth=6, max_elements=20):
    """Mirror of AppleScriptActuator.get_accessibility_elements() — what element finding sees."""
    script = r"""
function run(argv) {
    var sysEvents = Application("System Events");
    var proc;
    if (argv.length > 0 && argv[0]) {
        proc = sysEvents.processes.byName(argv[0]);
    } else {
        var frontmost = sysEvents.processes.whose({frontmost: true});
        if (frontmost.length === 0) return "[]";
        proc = frontmost[0];
    }

    var elements = [];
    var MAX_DEPTH = parseInt(argv[1] || "6");
    var MAX_ELEMENTS = parseInt(argv[2] || "20");
    var interactiveRoles = [
        "AXButton", "AXLink", "AXTextField", "AXTextArea",
        "AXCheckBox", "AXRadioButton", "AXPopUpButton",
        "AXComboBox", "AXMenuItem", "AXTab", "AXIncrementor"
    ];

    var skipRoles = ["AXMenuBar", "AXMenu", "AXMenuItem", "AXMenuBarItem"];

    function walk(elem, depth) {
        if (depth > MAX_DEPTH || elements.length >= MAX_ELEMENTS) return;
        try {
            var role = elem.role();
            if (skipRoles.indexOf(role) !== -1) return;
            if (interactiveRoles.indexOf(role) !== -1) {
                var pos = elem.position();
                var size = elem.size();
                if (pos && size && size[0] > 0 && size[1] > 0) {
                    elements.push({
                        label: (function() {
                            try { return elem.title() || ""; } catch(e) { return ""; }
                        })() || (function() {
                            try { return elem.description() || ""; } catch(e) { return ""; }
                        })() || (function() {
                            try { var v = elem.value(); return typeof v === "string" ? v : ""; } catch(e) { return ""; }
                        })(),
                        role: role,
                        x: pos[0], y: pos[1],
                        width: size[0], height: size[1],
                        center_x: pos[0] + Math.round(size[0] / 2),
                        center_y: pos[1] + Math.round(size[1] / 2)
                    });
                }
            }
            var children = elem.uiElements();
            for (var i = 0; i < children.length; i++) {
                walk(children[i], depth + 1);
            }
        } catch(e) {}
    }

    walk(proc, 0);
    return JSON.stringify(elements);
}
"""
    args = ["osascript", "-l", "JavaScript", "-e", script]
    if app_name:
        args.append(app_name)
    else:
        args.append("")
    args.append(str(max_depth))
    args.append(str(max_elements))
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=120)
        if result.returncode != 0 or not result.stdout.strip():
            return []
        return json.loads(result.stdout.strip())
    except Exception as e:
        return [{"error": str(e)}]


def get_missing_properties(app_name=""):
    """Properties we DON'T currently collect but COULD — the gap analysis."""
    script = r"""
function run(argv) {
    var sysEvents = Application("System Events");
    var proc;
    if (argv.length > 0 && argv[0]) {
        proc = sysEvents.processes.byName(argv[0]);
    } else {
        var frontmost = sysEvents.processes.whose({frontmost: true});
        if (frontmost.length === 0) return JSON.stringify({error: "no frontmost app"});
        proc = frontmost[0];
    }

    var result = {};

    // 1. Focused element (AXFocusedUIElement) — the biggest gap
    try {
        var focused = proc.focusedUIElement();
        if (focused) {
            result.focused_element = {
                role: (function() { try { return focused.role(); } catch(e) { return ""; } })(),
                title: (function() { try { return focused.title() || ""; } catch(e) { return ""; } })(),
                value: (function() {
                    try {
                        var v = focused.value();
                        return (v === null || v === undefined) ? null : String(v);
                    } catch(e) { return null; }
                })(),
                description: (function() { try { return focused.description() || ""; } catch(e) { return ""; } })(),
                placeholder: (function() { try { return focused.placeholderValue() || ""; } catch(e) { return ""; } })(),
                selected_text: (function() { try { return focused.selectedText() || ""; } catch(e) { return ""; } })(),
                enabled: (function() { try { return focused.enabled(); } catch(e) { return null; } })(),
                position: (function() { try { return focused.position(); } catch(e) { return null; } })(),
                size: (function() { try { return focused.size(); } catch(e) { return null; } })(),
            };
        } else {
            result.focused_element = null;
        }
    } catch(e) {
        result.focused_element = {error: e.message};
    }

    // 2. Selected text range (if any)
    try {
        var focused2 = proc.focusedUIElement();
        if (focused2) {
            try {
                var selRange = focused2.selectedTextRange();
                result.selected_text_range = selRange ? String(selRange) : null;
            } catch(e) {
                result.selected_text_range = null;
            }
        }
    } catch(e) {
        result.selected_text_range = null;
    }

    // 3. Walk ALL elements (not just interactive) with full properties — first 50
    var allElements = [];
    var skipAll = ["AXMenuBar", "AXMenu", "AXMenuItem", "AXMenuBarItem"];
    function walkAll(elem, depth) {
        if (depth > 8 || allElements.length >= 200) return;
        try {
            var role = elem.role();
            if (skipAll.indexOf(role) !== -1) return;
            var pos = (function() { try { return elem.position(); } catch(e) { return null; } })();
            var size = (function() { try { return elem.size(); } catch(e) { return null; } })();
            allElements.push({
                role: role,
                title: (function() { try { return elem.title() || ""; } catch(e) { return ""; } })(),
                value: (function() {
                    try {
                        var v = elem.value();
                        if (v === null || v === undefined) return null;
                        if (typeof v === "boolean" || typeof v === "number") return v;
                        return String(v).substring(0, 200);
                    } catch(e) { return null; }
                })(),
                description: (function() { try { return elem.description() || ""; } catch(e) { return ""; } })(),
                enabled: (function() { try { return elem.enabled(); } catch(e) { return null; } })(),
                focused: (function() { try { return elem.focused(); } catch(e) { return null; } })(),
                subrole: (function() { try { return elem.subrole() || ""; } catch(e) { return ""; } })(),
                x: pos ? pos[0] : null,
                y: pos ? pos[1] : null,
                width: size ? size[0] : null,
                height: size ? size[1] : null,
                depth: depth,
            });
            var children = elem.uiElements();
            for (var i = 0; i < children.length; i++) {
                walkAll(children[i], depth + 1);
            }
        } catch(e) {}
    }
    walkAll(proc, 0);
    result.all_elements_count = allElements.length;
    result.all_elements = allElements;

    return JSON.stringify(result);
}
"""
    args = ["osascript", "-l", "JavaScript", "-e", script]
    args.append(app_name or "")
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            return {"error": result.stderr.strip()[:500]}
        return json.loads(result.stdout.strip())
    except subprocess.TimeoutExpired:
        return {"error": "timeout (15s)"}
    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Dump accessibility layer info")
    parser.add_argument("--app", default="", help="App name (default: frontmost)")
    parser.add_argument("--out", default="", help="Output file (default: stdout + /tmp/ax_dump.json)")
    parser.add_argument("--deep", action="store_true", help="Include missing properties analysis")
    parser.add_argument("--max-depth", type=int, default=6, help="Max AX tree depth")
    parser.add_argument("--max-elements", type=int, default=200, help="Max elements to collect")
    args = parser.parse_args()

    print(f"Dumping accessibility info at {datetime.now().isoformat()}")
    print(f"App filter: {args.app or '(frontmost)'}")
    print()

    # Section 1: get_state()
    print("=" * 60)
    print("SECTION 1: get_state() — what tier-1 verification sees")
    print("=" * 60)
    state = get_state(args.app)
    for k, v in state.items():
        print(f"  {k}: {v}")
    print()

    # Section 2: get_accessibility_elements()
    print("=" * 60)
    print("SECTION 2: get_accessibility_elements() — what element finding sees")
    print("=" * 60)
    elements = get_accessibility_elements(
        args.app, max_depth=args.max_depth, max_elements=args.max_elements
    )
    print(f"  Found {len(elements)} interactive elements:")
    for i, el in enumerate(elements):
        label = el.get("label", "")[:50]
        role = el.get("role", "")
        cx, cy = el.get("center_x", 0), el.get("center_y", 0)
        w, h = el.get("width", 0), el.get("height", 0)
        print(f"  [{i:2d}] {role:<20s} ({cx:4d},{cy:4d}) {w:4d}x{h:<4d} '{label}'")
    print()

    # Section 3: What's missing
    missing_props = {}
    if args.deep:
        print("=" * 60)
        print("SECTION 3: Missing properties — what we COULD collect")
        print("=" * 60)
        missing_props = get_missing_properties(args.app)

        focused = missing_props.get("focused_element")
        if focused and not focused.get("error"):
            print("  Focused element:")
            for k, v in focused.items():
                if v is not None and v != "":
                    print(f"    {k}: {v}")
        elif focused and focused.get("error"):
            print(f"  Focused element: ERROR — {focused['error']}")
        else:
            print("  Focused element: None (no element has focus)")
        print()

        all_els = missing_props.get("all_elements", [])
        print(f"  All elements (including non-interactive): {len(all_els)}")
        for i, el in enumerate(all_els[:30]):
            role = el.get("role", "")
            title = el.get("title", "")[:30]
            value = el.get("value")
            enabled = el.get("enabled")
            focused = el.get("focused")
            depth = el.get("depth", 0)
            indent = "  " * depth
            extras = []
            if value is not None:
                extras.append(f"val={str(value)[:30]}")
            if enabled is False:
                extras.append("DISABLED")
            if focused is True:
                extras.append("FOCUSED")
            extra_str = f" [{', '.join(extras)}]" if extras else ""
            print(f"  {indent}[{i:2d}] {role:<20s} '{title}'{extra_str}")
        if len(all_els) > 30:
            print(f"  ... and {len(all_els) - 30} more")
    else:
        print("(Use --deep to see missing properties analysis)")

    # Save to file
    dump = {
        "timestamp": datetime.now().isoformat(),
        "app_filter": args.app or "(frontmost)",
        "get_state": state,
        "interactive_elements": elements,
        "interactive_count": len(elements),
    }
    if args.deep:
        dump["missing_properties"] = missing_props

    out_path = args.out or "/tmp/ax_dump.json"
    with open(out_path, "w") as f:
        json.dump(dump, f, indent=2, default=str)
    print(f"\nFull dump saved to: {out_path}")


if __name__ == "__main__":
    main()
