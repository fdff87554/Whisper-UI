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
# failure means the entrypoint never runs, so the fix has to live on the host.
# This script only heals worker-rocm — frontend/redis/io/llm need no GPU and
# recover on their own via `restart: unless-stopped`.
#
# Install (host systemd timer):
#   sudo install -m0755 docker/whisper-rocm-heal.sh /usr/local/bin/whisper-rocm-heal.sh
#
#   sudo tee /etc/systemd/system/whisper-rocm-heal.service >/dev/null <<'UNIT'
#   [Unit]
#   Description=Heal whisper worker-rocm (bring up if fewer than desired run)
#   After=docker.service
#   Wants=docker.service
#   [Service]
#   Type=oneshot
#   User=ubuntu
#   # Override the deploy dir / desired count here if they differ:
#   #   Environment=WHISPER_DEPLOY_DIR=/home/ubuntu/whisper-ui-deploy
#   #   Environment=WHISPER_ROCM_SCALE=1
#   ExecStart=/usr/local/bin/whisper-rocm-heal.sh
#   UNIT
#
#   sudo tee /etc/systemd/system/whisper-rocm-heal.timer >/dev/null <<'UNIT'
#   [Unit]
#   Description=Periodic heal for whisper worker-rocm
#   [Timer]
#   OnBootSec=45s
#   OnUnitActiveSec=2min
#   AccuracySec=15s
#   [Install]
#   WantedBy=timers.target
#   UNIT
#
#   sudo systemctl daemon-reload && sudo systemctl enable --now whisper-rocm-heal.timer
#
# Env:
#   WHISPER_DEPLOY_DIR  deploy dir holding compose.yml + .env (default ~/whisper-ui-deploy)
#   WHISPER_ROCM_SCALE  desired worker-rocm replicas (default 1)
#   WHISPER_HEAL_LOG    log file (default $WHISPER_DEPLOY_DIR/whisper-rocm-heal.log)
set -euo pipefail

DEPLOY="${WHISPER_DEPLOY_DIR:-$HOME/whisper-ui-deploy}"
DESIRED="${WHISPER_ROCM_SCALE:-1}"
LOG="${WHISPER_HEAL_LOG:-$DEPLOY/whisper-rocm-heal.log}"

case "$DESIRED" in
'' | *[!0-9]*)
	echo "$(date -Is) heal: invalid WHISPER_ROCM_SCALE=$DESIRED (need a non-negative integer)" >&2
	exit 1
	;;
esac

cd "$DEPLOY" || {
	echo "$(date -Is) heal: deploy dir not found: $DEPLOY" >&2
	exit 1
}

# Wait up to ~120s for the GPU device nodes so the compose up can succeed.
# Match any render node (renderD128, renderD129, ...), not just the gfx1151 one.
devices_ready() {
	[ -e /dev/kfd ] || return 1
	for node in /dev/dri/renderD*; do
		[ -e "$node" ] && return 0
	done
	return 1
}
ready=0
for _ in $(seq 1 60); do
	if devices_ready; then
		ready=1
		break
	fi
	sleep 2
done
if [ "$ready" -ne 1 ]; then
	echo "$(date -Is) heal: GPU device nodes still absent after wait; aborting" >>"$LOG"
	exit 1
fi

# Count only THIS compose project's worker-rocm, not other deployments' by name.
if ! running=$(docker compose --profile rocm ps -q --status running worker-rocm | wc -l); then
	echo "$(date -Is) heal: 'docker compose ps' failed" >>"$LOG"
	exit 1
fi

if [ "$running" -lt "$DESIRED" ]; then
	echo "$(date -Is) heal: $running/$DESIRED worker-rocm running, running compose up" >>"$LOG"
	if ! docker compose --profile rocm up -d --no-recreate --scale worker-rocm="$DESIRED" worker-rocm >>"$LOG" 2>&1; then
		echo "$(date -Is) heal: compose up failed" >>"$LOG"
		exit 1
	fi
fi
