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

WORKER_QUEUES="${WORKER_QUEUES:-whisper:gpu whisper:io whisper:cpu whisper:llm default}"

# A GPU context cannot be re-initialised in a forked subprocess, so GPU
# workers must run jobs in the main process via SimpleWorker (no per-job
# fork) instead of RQ's default forking Worker. This applies to both cuda
# and rocm: ROCm's PyTorch is a HIP build that still uses the torch.cuda API,
# so it hits the identical "Cannot re-initialize CUDA in forked subprocess"
# error (align/diarize fail). CPU workers keep the default forking Worker.
# DEVICE is always set explicitly per worker in compose; "auto" is treated as
# non-GPU here (no deployed worker uses it).
WORKER_CLASS_ARG=""
case "${DEVICE:-auto}" in
cuda | rocm)
	WORKER_CLASS_ARG="--worker-class rq.SimpleWorker"
	echo "Using SimpleWorker (DEVICE=${DEVICE}) to avoid GPU fork-initialization errors"
	;;
esac

# Optional idle self-exit. When WORKER_MAX_IDLE_TIME (seconds) is >0 the worker
# quits after that long without a job; paired with compose `restart:
# unless-stopped` a fresh process respawns. This is the only way a long-lived
# SimpleWorker (cuda/rocm) hands its GPU context + RSS back to the OS between
# sessions — torch.cuda.empty_cache() frees model weights but never the context.
WORKER_MAX_IDLE_ARG=""
if [ -n "${WORKER_MAX_IDLE_TIME:-}" ] && [ "${WORKER_MAX_IDLE_TIME}" != "0" ]; then
	WORKER_MAX_IDLE_ARG="--max-idle-time ${WORKER_MAX_IDLE_TIME}"
	echo "Worker will exit after ${WORKER_MAX_IDLE_TIME}s idle (restart policy reclaims GPU/RSS)"
fi

echo "Starting RQ worker on queues: ${WORKER_QUEUES}"
# shellcheck disable=SC2086
exec python -m whisper_ui.worker worker \
	--url "${REDIS_URL:-redis://redis:6379/0}" \
	--name "whisper-worker-$(hostname)" \
	${WORKER_CLASS_ARG} \
	${WORKER_MAX_IDLE_ARG} \
	${WORKER_QUEUES}
