#!/bin/bash
# Run an agent task inside a DISPOSABLE clone of the provisioned macOS VM.
# The host desktop is never touched; the clone is deleted afterwards
# (kept with --keep or on failure for debugging).
#
# Usage:
#   scripts/vm/run_vm_e2e.sh "Open Calculator"
#   scripts/vm/run_vm_e2e.sh --keep "Open Safari and go to apple.com"
#   scripts/vm/run_vm_e2e.sh --graphics "..."   # watch the VM in a window
set -euo pipefail

TART="${TART:-/opt/homebrew/bin/tart}"
BASE_VM="${VM_NAME:-agent-vm-base}"
RUN_VM="agent-vm-run-$$"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10)
KEY=~/.ssh/id_ed25519_agentvm

KEEP=0
GRAPHICS=0
ARGS=()
for arg in "$@"; do
    case "$arg" in
        --keep) KEEP=1 ;;
        --graphics) GRAPHICS=1 ;;
        *) ARGS+=("$arg") ;;
    esac
done
[ ${#ARGS[@]} -ge 1 ] || { echo "usage: $0 [--keep] [--graphics] \"<task prompt>\""; exit 2; }
PROMPT="${ARGS[0]}"

log() { echo "[vm_e2e] $*"; }
cleanup() {
    if [ "$KEEP" = 0 ]; then
        "$TART" stop "$RUN_VM" >/dev/null 2>&1 || true
        "$TART" delete "$RUN_VM" >/dev/null 2>&1 || true
    else
        log "VM kept: $RUN_VM (delete with: tart delete $RUN_VM)"
    fi
}
trap cleanup EXIT

log "cloning $BASE_VM -> $RUN_VM (APFS, fast)"
"$TART" clone "$BASE_VM" "$RUN_VM"

if [ "$GRAPHICS" = 1 ]; then
    nohup "$TART" run "$RUN_VM" >/tmp/tart_run_$RUN_VM.log 2>&1 &
else
    nohup "$TART" run "$RUN_VM" --no-graphics >/tmp/tart_run_$RUN_VM.log 2>&1 &
fi

IP=""
for _ in $(seq 1 90); do
    IP=$("$TART" ip "$RUN_VM" 2>/dev/null || true)
    [ -n "$IP" ] && break
    sleep 2
done
[ -n "$IP" ] || { echo "VM did not boot"; exit 1; }
log "VM ip: $IP — waiting for SSH + desktop"
for _ in $(seq 1 45); do
    ssh "${SSH_OPTS[@]}" -i "$KEY" admin@"$IP" true 2>/dev/null && break
    sleep 2
done
# Give the login session a moment to finish composing the desktop
sleep 8

OUT_DIR="$REPO_ROOT/logs/vm_e2e/$(date -u +%Y%m%d_%H%M%S)"
mkdir -p "$OUT_DIR"

log "running task: $PROMPT"
REMOTE_CMD=$(printf 'cd agent && .venv/bin/python -m automation_agent %q --status-ui off' "$PROMPT")
set +e
ssh "${SSH_OPTS[@]}" -i "$KEY" admin@"$IP" "$REMOTE_CMD" 2>&1 | tee "$OUT_DIR/agent_output.txt"
EXIT_CODE=${PIPESTATUS[0]}
set -e

log "collecting artifacts"
ssh "${SSH_OPTS[@]}" -i "$KEY" admin@"$IP" "screencapture -x /tmp/final.png" || true
scp "${SSH_OPTS[@]}" -i "$KEY" admin@"$IP":/tmp/final.png "$OUT_DIR/final_screen.png" 2>/dev/null || true
rsync -a -e "ssh ${SSH_OPTS[*]} -i $KEY" admin@"$IP":agent/logs/ "$OUT_DIR/agent_logs/" 2>/dev/null || true

log "exit=$EXIT_CODE artifacts=$OUT_DIR"
[ "$EXIT_CODE" = 0 ] && KEEP=$KEEP || KEEP=1   # keep VM on failure for debugging
exit "$EXIT_CODE"
