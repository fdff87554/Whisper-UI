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

# Queues to listen on. Defaults to the full set so a single container can
# still run every pipeline stage, which is the common single-host layout.
# The multi-worker docker-compose topology overrides this via WORKER_QUEUES
# to specialise containers per resource class (io / gpu / cpu). "default"
# is RQ's standard queue name; keeping every worker subscribed lets an
# operator drop ad-hoc maintenance jobs without learning the resource-
# class queue names.
WORKER_QUEUES="${WORKER_QUEUES:-whisper:gpu whisper:io whisper:cpu default}"

# Start RQ worker
echo "Starting RQ worker on queues: ${WORKER_QUEUES}"
# shellcheck disable=SC2086
exec python -m rq.cli worker \
	--url "${REDIS_URL:-redis://redis:6379/0}" \
	--name "whisper-worker-$(hostname)" \
	${WORKER_QUEUES}
