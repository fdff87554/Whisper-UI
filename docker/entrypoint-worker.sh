#!/bin/bash
set -euo pipefail

echo "=== Whisper UI Worker ==="

# Log GPU availability (device detection handled in Python)
if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
	echo "GPU detected:"
	nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
else
	echo "No GPU detected."
fi

echo "Device: ${DEVICE:-auto}, Compute type: ${COMPUTE_TYPE:-int8_float16}"

# Check model cache
MODEL_DIR="${HF_HOME:-/cache/huggingface}"
echo "Model cache directory: ${MODEL_DIR}"

# Start RQ worker
echo "Starting RQ worker..."
exec python -m rq.cli worker \
	--url "${REDIS_URL:-redis://redis:6379/0}" \
	--name "whisper-worker-$(hostname)" \
	default
