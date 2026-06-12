#!/bin/bash
# One-time provisioning of a macOS VM for full-fidelity agent e2e testing.
# The VM runs the real macOS desktop (Safari, Calculator, AppleScript,
# accessibility) — the host desktop is never touched.
#
# Requires: Apple Silicon, tart (brew install cirruslabs/cli/tart),
#           sshpass (brew install sshpass), ~45GB free disk for the base image.
#
# Steps: clone cirruslabs base image -> boot -> install SSH key ->
#        sync repo -> create venv -> write .env -> grant TCC permissions
#        (SIP is disabled in cirruslabs images, so TCC.db is writable) ->
#        verify capabilities -> shut down (becomes the golden base).
#
# Rerunnable: every step is idempotent.
set -euo pipefail

TART="${TART:-/opt/homebrew/bin/tart}"
BASE_IMAGE="ghcr.io/cirruslabs/macos-sequoia-base:latest"
VM="${VM_NAME:-agent-vm-base}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10)

log() { echo "[setup_vm] $*"; }

# --- 1. VM exists -----------------------------------------------------------
if ! "$TART" list 2>/dev/null | grep -q "^local *$VM"; then
    log "cloning $BASE_IMAGE -> $VM (large download on first run)"
    "$TART" clone "$BASE_IMAGE" "$VM"
fi
"$TART" set "$VM" --cpu 4 --memory 8192 --display 1280x800

# --- 2. Boot ----------------------------------------------------------------
if ! "$TART" ip "$VM" >/dev/null 2>&1; then
    log "booting $VM headless"
    nohup "$TART" run "$VM" --no-graphics >/tmp/tart_run_$VM.log 2>&1 &
fi
IP=""
for _ in $(seq 1 60); do
    IP=$("$TART" ip "$VM" 2>/dev/null || true)
    [ -n "$IP" ] && break
    sleep 2
done
[ -n "$IP" ] || { echo "VM did not get an IP"; exit 1; }
log "VM ip: $IP"

# --- 3. SSH key install (password auth via expect, once) ---------------------
[ -f ~/.ssh/id_ed25519_agentvm ] || ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519_agentvm -C agentvm
PUB=$(cat ~/.ssh/id_ed25519_agentvm.pub)
for _ in $(seq 1 30); do
    if "$REPO_ROOT/scripts/vm/vmssh" "$IP" "mkdir -p ~/.ssh && grep -qF '$PUB' ~/.ssh/authorized_keys 2>/dev/null || echo '$PUB' >> ~/.ssh/authorized_keys" 2>/dev/null; then
        break
    fi
    sleep 4
done
VSSH() { ssh "${SSH_OPTS[@]}" -i ~/.ssh/id_ed25519_agentvm admin@"$IP" "$@"; }
VSSH true || { echo "key-based SSH failed"; exit 1; }
log "ssh ready"

# --- 4. SIP / environment sanity ---------------------------------------------
log "SIP status: $(VSSH csrutil status || true)"

# --- 5. Sync repo -------------------------------------------------------------
log "syncing repo"
rsync -a --delete \
    --exclude .venv --exclude .venv-molmo2 --exclude logs --exclude .git \
    --exclude '__pycache__' --exclude '.pytest_cache' \
    -e "ssh ${SSH_OPTS[*]} -i $HOME/.ssh/id_ed25519_agentvm" \
    "$REPO_ROOT/" admin@"$IP":agent/

# --- 6. Python env -------------------------------------------------------------
# Bare /usr/bin/python3 is an xcrun shim on CLT-less images and hangs a
# headless session — use Homebrew python (preinstalled in cirruslabs images).
log "installing brew python + creating venv (first run takes a few minutes)"
VSSH 'export PATH=/opt/homebrew/bin:$PATH; which python3.12 >/dev/null 2>&1 || brew install -q python@3.12'
VSSH 'export PATH=/opt/homebrew/bin:$PATH; cd agent && (test -x .venv/bin/python || python3.12 -m venv .venv) && .venv/bin/python -m pip -q install --upgrade pip && .venv/bin/python -m pip -q install -e ".[gemini]"'

# --- 7. .env for the VM ---------------------------------------------------------
log "writing VM .env (API keys copied from host .env)"
TMPENV=$(mktemp)
grep -E '^(AGENT_MODEL_PROVIDER|AGENT_GEMINI_API_KEY|GEMINI_API_KEY|AGENT_ANTHROPIC_API_KEY|ANTHROPIC_API_KEY|OPENAI_API_KEY|AGENT_GROUNDING_MODEL_PROVIDER)=' "$REPO_ROOT/.env" > "$TMPENV" || true
cat >> "$TMPENV" <<'EOF'
AGENT_STATUS_UI=off
AGENT_LOG_DIR=logs
AGENT_REQUIRE_CONFIRMATION=false
EOF
scp "${SSH_OPTS[@]}" -i ~/.ssh/id_ed25519_agentvm "$TMPENV" admin@"$IP":agent/.env
rm -f "$TMPENV"

# --- 8. TCC grants (Screen Recording, Accessibility, Apple Events) -------------
log "granting TCC permissions inside VM"
scp "${SSH_OPTS[@]}" -i ~/.ssh/id_ed25519_agentvm \
    "$REPO_ROOT/scripts/vm/grant_tcc.py" admin@"$IP":/tmp/grant_tcc.py
# Use the venv python (real interpreter, has sqlite3) — /usr/bin/python3 is
# an xcrun shim when Xcode CLT is absent.
VSSH 'sudo ~/agent/.venv/bin/python /tmp/grant_tcc.py'

# --- 9. Capability smoke test ----------------------------------------------------
log "running capability smoke test"
scp "${SSH_OPTS[@]}" -i ~/.ssh/id_ed25519_agentvm \
    "$REPO_ROOT/scripts/vm/vm_smoke.sh" admin@"$IP":/tmp/vm_smoke.sh
VSSH 'bash /tmp/vm_smoke.sh' || {
    echo "[setup_vm] smoke test FAILED — VM kept running for debugging (tart ip $VM)"
    exit 1
}

log "stopping VM — the golden base must be stopped before cloning runs"
"$TART" stop "$VM" 2>/dev/null || true

log "provisioning complete. VM '$VM' is the golden base."
log "run e2e with: scripts/vm/run_vm_e2e.sh \"Open Calculator\""
