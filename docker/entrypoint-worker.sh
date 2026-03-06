#!/bin/bash
set -euo pipefail

echo "=== Whisper UI GPU Worker ==="

# Check GPU availability
if command -v nvidia-smi &>/dev/null; then
	echo "GPU detected:"
	nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
else
	echo "WARNING: No GPU detected. Running in CPU mode."
	export DEVICE=cpu
fi

# Check model cache
MODEL_DIR="${HF_HOME:-/cache/huggingface}"
echo "Model cache directory: ${MODEL_DIR}"

# Start RQ worker
echo "Starting RQ worker..."
exec python -m rq.cli worker \
	--url "${REDIS_URL:-redis://redis:6379/0}" \
	--name "whisper-worker-$(hostname)" \
	default
