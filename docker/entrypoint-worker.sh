#!/bin/bash
set -euo pipefail

echo "=== Whisper UI Worker ==="

# Log GPU availability (device detection handled in Python)
if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
	echo "NVIDIA GPU detected:"
	nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
elif command -v rocm-smi &>/dev/null && rocm-smi &>/dev/null; then
	echo "AMD GPU detected:"
	rocm-smi --showproductname 2>/dev/null | grep -Ei "Card Series|GFX Version" || true
else
	echo "No GPU detected."
fi

echo "Device: ${DEVICE:-auto}, Compute type: ${COMPUTE_TYPE:-int8_float16}"

# Check model cache
MODEL_DIR="${HF_HOME:-/cache/huggingface}"
echo "Model cache directory: ${MODEL_DIR}"

WORKER_QUEUES="${WORKER_QUEUES:-whisper:gpu whisper:io whisper:cpu default}"

# CUDA cannot be re-initialised in a forked subprocess. When the device is
# set to cuda, use SimpleWorker which runs jobs in the main process instead
# of forking.
WORKER_CLASS_ARG=""
if [ "${DEVICE:-auto}" = "cuda" ]; then
	WORKER_CLASS_ARG="--worker-class rq.SimpleWorker"
	echo "Using SimpleWorker to avoid CUDA fork issues"
fi

echo "Starting RQ worker on queues: ${WORKER_QUEUES}"
# shellcheck disable=SC2086
exec python -m whisper_ui.worker worker \
	--url "${REDIS_URL:-redis://redis:6379/0}" \
	--name "whisper-worker-$(hostname)" \
	${WORKER_CLASS_ARG} \
	${WORKER_QUEUES}
