#!/bin/bash
# Bring worker-rocm back up when fewer than the desired number are running.
#
# Why this exists: worker-rocm mounts /dev/kfd + /dev/dri, which Docker resolves
# at container CREATE time. On a host reboot / GPU driver reload the container
# can try to start before those device nodes reappear, failing to create with
# `exit 128 ... custom device "/dev/dri": no such file`. Docker's
# `restart: unless-stopped` retries rapidly and then GIVES UP, leaving the
# worker down indefinitely while every other service (which needs no GPU) stays
# healthy — a silent multi-day whisper:gpu outage (observed 2026-07).
#
# An in-container entrypoint wait cannot fix this: the create-time device
# failure means the entrypoint never runs. The fix has to live on the host.
# Run this from a systemd timer (see docs/gpu-worker-recovery.md); it waits for
# the device nodes, then re-runs `compose up` only if a worker-rocm is missing.
#
# Env:
#   WHISPER_DEPLOY_DIR  deploy dir holding compose.yml + .env (default ~/whisper-ui-deploy)
#   WHISPER_ROCM_SCALE  desired worker-rocm replicas (default 1)
#   WHISPER_IO_SCALE    desired worker-io replicas (default 2)
#   WHISPER_HEAL_LOG    log file (default $WHISPER_DEPLOY_DIR/whisper-rocm-heal.log)
set -u

DEPLOY="${WHISPER_DEPLOY_DIR:-$HOME/whisper-ui-deploy}"
DESIRED="${WHISPER_ROCM_SCALE:-1}"
IO_SCALE="${WHISPER_IO_SCALE:-2}"
LOG="${WHISPER_HEAL_LOG:-$DEPLOY/whisper-rocm-heal.log}"

# Wait up to ~120s for the GPU device nodes so the compose up can succeed.
for _ in $(seq 1 60); do
	[ -e /dev/kfd ] && [ -e /dev/dri/renderD128 ] && break
	sleep 2
done

running=$(docker ps --filter 'name=worker-rocm' --format '{{.Names}}' | wc -l)
if [ "$running" -lt "$DESIRED" ]; then
	cd "$DEPLOY" || exit 1
	echo "$(date -Is) heal: $running/$DESIRED worker-rocm running, running compose up" >>"$LOG"
	docker compose --profile rocm --profile io --profile llm-worker up -d --no-recreate \
		--scale worker-io="$IO_SCALE" --scale worker-rocm="$DESIRED" \
		frontend worker-rocm worker-io worker-llm >>"$LOG" 2>&1 ||
		echo "$(date -Is) heal: compose up failed" >>"$LOG"
fi
