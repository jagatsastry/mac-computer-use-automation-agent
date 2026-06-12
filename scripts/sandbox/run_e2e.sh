#!/bin/bash
# Rerunnable sandboxed e2e suite for the automation agent.
# Never touches the host desktop — all actions land in the Docker sandbox.
set -euo pipefail
cd "$(dirname "$0")/../.."
exec .venv/bin/python scripts/sandbox/run_e2e.py "$@"
