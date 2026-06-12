#!/bin/bash
# Boots the sandbox desktop: Xvfb display, window manager, VNC mirror,
# CDP port forward, and the local test website.
set -e

RES="${SANDBOX_RESOLUTION:-1024x768x24}"

Xvfb :99 -screen 0 "$RES" -nolisten tcp &
# Wait for the X server to accept connections
for i in $(seq 1 50); do
    if xdpyinfo -display :99 >/dev/null 2>&1; then break; fi
    sleep 0.1
done

openbox &

# Recognizable desktop-blue background — a blank desktop must not read as
# "completely black screen" to vision models / the planner
xsetroot -solid "#3a6ea5" 2>/dev/null || true

x11vnc -display :99 -nopw -forever -shared -rfbport 5900 -quiet -bg

# Chromium binds CDP to 127.0.0.1 only in headed mode; socat re-exposes
# it on 0.0.0.0:9223 so the host can reach it through the published port.
socat TCP-LISTEN:9223,fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:9222 &

cd /opt/testsite && python3 -m http.server 8000 --bind 0.0.0.0 &

echo "SANDBOX_READY display=:99 res=$RES"
# Keep PID 1 alive; reap children
wait -n || true
exec sleep infinity
