#!/bin/bash
set -euo pipefail

echo "=== Whisper UI Worker ==="

# Check GPU availability
if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
	echo "GPU detected:"
	nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
else
	echo "No GPU detected. Running in CPU mode."
	export DEVICE=cpu
fi

# Validate COMPUTE_TYPE compatibility with DEVICE
if [ "${DEVICE:-cuda}" = "cpu" ]; then
	case "${COMPUTE_TYPE:-auto}" in
	int8_float16 | float16)
		echo "WARNING: COMPUTE_TYPE=${COMPUTE_TYPE} is not supported on CPU. Falling back to int8."
		export COMPUTE_TYPE=int8
		;;
	esac
fi

echo "Device: ${DEVICE:-cuda}, Compute type: ${COMPUTE_TYPE:-auto}"

# Check model cache
MODEL_DIR="${HF_HOME:-/cache/huggingface}"
echo "Model cache directory: ${MODEL_DIR}"

# Start RQ worker
echo "Starting RQ worker..."
exec python -m rq.cli worker \
	--url "${REDIS_URL:-redis://redis:6379/0}" \
	--name "whisper-worker-$(hostname)" \
	default
