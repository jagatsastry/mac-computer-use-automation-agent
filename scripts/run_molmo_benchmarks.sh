#!/bin/bash
# Run GroundCUA benchmark sequentially for each Molmo model.
# Only one MLX model can run at a time due to memory constraints.
set -e

VENV=".venv-molmo2/bin/python"
SERVER="scripts/mlx_vlm_server.py"
BENCH=".venv/bin/python scripts/benchmark_groundcua.py"
PORT=8092
N=50
SEED=42

kill_server() {
    kill $(lsof -ti :$PORT) 2>/dev/null || true
    sleep 3
}

start_server() {
    local model="$1"
    echo "=== Starting server: $model on port $PORT ==="
    kill_server
    PYTHONUNBUFFERED=1 $VENV $SERVER --model "$model" --port $PORT > /tmp/molmo_server.log 2>&1 &
    SERVER_PID=$!
    echo "PID: $SERVER_PID"

    # Wait for ready
    for i in $(seq 1 60); do
        if curl -s "http://localhost:$PORT/v1/models" > /dev/null 2>&1; then
            echo "Server ready after ${i}s"
            return 0
        fi
        sleep 2
    done
    echo "ERROR: Server failed to start"
    cat /tmp/molmo_server.log | tail -20
    return 1
}

run_bench() {
    local model_name="$1"
    echo ""
    echo "=========================================="
    echo "BENCHMARKING: $model_name"
    echo "=========================================="
    PYTHONUNBUFFERED=1 $BENCH --n $N --seed $SEED --models "$model_name" --save-screenshots 2>&1 | \
        grep -vE "FutureWarning|NotOpenSSLWarning|warnings.warn|thought_signature|^\s*$"
}

echo "GroundCUA Molmo Benchmark Suite"
echo "N=$N, seed=$SEED"
echo ""

# 1. Molmo2 (already uses port 8092)
start_server "mlx-community/Molmo2-8B-5bit"
run_bench "molmo2"

# 2. MolmoPoint-8B-4bit
start_server "mlx-community/MolmoPoint-8B-4bit"
run_bench "molmo-point"

# 3. MolmoPoint-GUI-8B
start_server "allenai/MolmoPoint-GUI-8B"
run_bench "molmo-point-gui"

kill_server
echo ""
echo "All benchmarks complete."
