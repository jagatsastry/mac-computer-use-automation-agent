# Sandboxed Linux desktop for testing the automation agent without
# touching the host's screen/keyboard. X11 desktop runs headless under
# Xvfb; the agent (on the host) actuates via `docker exec xdotool ...`
# and captures the screen via scrot.
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    SANDBOX_RESOLUTION=1024x768x24

RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11vnc \
    x11-utils \
    xdotool \
    scrot \
    openbox \
    chromium \
    galculator \
    mousepad \
    xterm \
    fonts-dejavu-core \
    python3 \
    socat \
    procps \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY testsite /opt/testsite
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY launch-browser /usr/local/bin/launch-browser
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/launch-browser

# 5900: VNC (watch the sandbox desktop live)
# 9223: Chrome DevTools Protocol (socat-forwarded from 127.0.0.1:9222)
# 8000: deterministic local test website
EXPOSE 5900 9223 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
