# Whisper-UI

Speech-to-text system using [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
(large-v3, INT8) with speaker diarization via
[pyannote-audio](https://github.com/pyannote/pyannote-audio),
a [FastAPI](https://fastapi.tiangolo.com/) + [htmx](https://htmx.org/) + [Alpine.js](https://alpinejs.dev/) web interface,
and Docker deployment (GPU / CPU).

> **Note:** The UI is in Traditional Chinese (繁體中文).

## Features

- Upload audio/video files for transcription
- Batch upload with automatic filtering of unsupported files
- Speaker diarization with pyannote speaker-diarization-3.1 (optional)
- Optional LLM text correction via Ollama (small Gemma model, per-job toggle)
- Real-time progress tracking via Redis
- Export to SRT, VTT, TXT, JSON, DOCX
- Batch download of results as ZIP
- Docker Compose deployment with GPU and CPU profiles

**Supported formats:** `.mp3`, `.wav`, `.m4a`, `.flac`, `.ogg`, `.wma`, `.aac`, `.opus`, `.mp4`, `.webm`, `.mkv`

## Architecture

```text
+------------------+     +------------------+     +------------------+
|     FastAPI      |     |      Redis       |     |     Worker       |
|  Frontend (htmx  |<--->|   (queue+state)  |<--->|  (RQ + Pipeline) |
|  + Alpine.js)    |     |                  |     |  (GPU or CPU)    |
+------------------+     +------------------+     +------------------+
        |                                                  |
        +------ Shared Volume: app-data (uploads/outputs/db) ------+
```

## Quick Start

### Prerequisites

- Docker and Docker Compose

**For GPU deployment (recommended for speed):**

- NVIDIA GPU with 8GB+ VRAM
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)

**Optional (for speaker diarization):**

- HuggingFace token ([get one here](https://huggingface.co/settings/tokens))
- Accept the model agreements:
  - <https://huggingface.co/pyannote/speaker-diarization-3.1>
  - <https://huggingface.co/pyannote/segmentation-3.0>

### GPU Deployment

```bash
cp .env.example .env
# Edit .env: set HF_TOKEN if you need speaker diarization

docker compose --profile gpu up -d
```

### CPU Deployment

```bash
cp .env.example .env
# Edit .env: uncomment and set WHISPER_MODEL=base
# (smaller models like 'base' or 'small' are recommended for CPU)

docker compose --profile cpu up -d
```

Open <http://localhost:8080> in your browser.

> **Production note:** the bundled Redis starts without authentication
> when `REDIS_PASSWORD` is unset (the compose snippet only adds
> `--requirepass` when the variable is non-empty). For any deployment
> reachable beyond the local Docker network — even on a trusted LAN —
> set `REDIS_PASSWORD` in `.env` before bringing the stack up.

### Pre-built Images

Pre-built Docker images are published to GHCR on each release.
`docker compose up` pulls them automatically; if unavailable, it falls back to a local build.

| Image                                     | Description           |
| ----------------------------------------- | --------------------- |
| `ghcr.io/fdff87554/whisper-ui-frontend`   | FastAPI web interface |
| `ghcr.io/fdff87554/whisper-ui-worker`     | GPU worker (CUDA)     |
| `ghcr.io/fdff87554/whisper-ui-worker-cpu` | CPU worker            |

**Pin a specific version** by setting `WHISPER_UI_VERSION` in your `.env` file:

```bash
WHISPER_UI_VERSION=1.3.0
```

**Build locally** instead of pulling (optional):

```bash
docker compose --profile gpu build
```

## Configuration

All settings are configured via environment variables (`.env` file):

| Variable             | Default                             | Description                                  |
| -------------------- | ----------------------------------- | -------------------------------------------- |
| `WHISPER_MODEL`      | `large-v3`                          | Whisper model variant (see model list below) |
| `COMPUTE_TYPE`       | `int8_float16` (GPU) / `int8` (CPU) | CTranslate2 compute type                     |
| `DEVICE`             | `cuda` (GPU) / `cpu` (CPU)          | Inference device                             |
| `BATCH_SIZE`         | `4`                                 | Transcription batch size                     |
| `LANGUAGE`           | `zh`                                | Default language code                        |
| `HF_TOKEN`           | (empty)                             | HuggingFace token for speaker diarization    |
| `PIP_INDEX_URL`      | (empty)                             | Custom PyPI mirror for Docker builds         |
| `WHISPER_UI_VERSION` | `latest`                            | Docker image version tag to pull             |

**Whisper models:** `tiny`, `tiny.en`, `base`, `base.en`, `small`, `small.en`, `medium`, `medium.en`, `large-v1`, `large-v2`, `large-v3`, `large-v3-turbo`

> **Tip:** For CPU deployment, use smaller models (`base`, `small`) for reasonable
> processing times. GPU deployment with `large-v3` and `int8_float16` gives the
> best accuracy-to-speed ratio.

### Worker topology and queues (advanced)

Each upload is dispatched as an RQ **DAG of sub-jobs** (one per pipeline
stage) rather than a single monolithic task. Sub-jobs are routed to
resource-class queues so a long-running IO or network stage never blocks
a GPU worker from picking up the next job:

| Queue         | Stages                                     |
| ------------- | ------------------------------------------ |
| `whisper:gpu` | `transcribe_align`, `diarize`              |
| `whisper:io`  | `download`, `preprocess`, `llm_correction` |
| `whisper:cpu` | `assign_speakers`, `postprocess`           |

**Single-container (default).** `docker compose --profile gpu up -d` keeps
the existing behaviour: `worker-gpu` listens to every queue so one
container drains the full pipeline end-to-end. You do not need to touch
any queue variables for this layout.

**Scaled topology.** To stop the GPU worker from picking up IO/LLM work,
add the `io` profile and narrow the GPU worker's queue set:

```bash
# .env
WORKER_GPU_QUEUES="whisper:gpu default"
WORKER_IO_QUEUES="whisper:io whisper:cpu default"

docker compose --profile gpu --profile io up -d
```

`worker-io` is a lightweight CPU container that drains `whisper:io`
(download / preprocess / llm_correction) in parallel with `worker-gpu`
running transcribe_align / diarize on the GPU. Two jobs enqueued back to
back overlap: job B can be downloading while job A is on the GPU, and
job A can be in llm_correction (hitting an external Ollama server) while
job B is already transcribing.

**Multi-GPU hosts.** When the DAG fans out transcribe_align and diarize
as sibling branches they will automatically run in parallel once you
provision more than one GPU worker. Example with two cards:

```bash
# Launch two GPU workers, each pinned to one card.
docker compose --profile gpu up -d --scale worker-gpu=2
# Or run named services and set WORKER_GPU_DEVICE_ID per container.
```

**Upgrading from v1.x to v2.0 (BREAKING).** The legacy single-task
`process_transcription` entry point has been removed in v2.0. Any RQ
sub-jobs enqueued under it from a v1.x worker will fail import when a
v2.0 worker picks them up. To upgrade:

1. Stop accepting new uploads (e.g. take the frontend offline) or wait
   for the dashboard to show no active jobs.
2. Let the existing workers drain whatever is in flight.
3. Pull the v2.0 images and `docker compose --profile gpu up -d`.

If a queue is non-empty when the worker is upgraded, drop the queues
manually before restart: `redis-cli -n 0 FLUSHDB` against the
`REDIS_URL` Redis only clears RQ state (uploads, the SQLite DB, and
saved transcripts are untouched).

### Queue / Timeout tuning (advanced)

The RQ job timeout is derived from the probed audio duration
(`duration * JOB_TIMEOUT_AUDIO_MULTIPLIER`) and clamped into
`[JOB_TIMEOUT_FLOOR, JOB_TIMEOUT_MAX]`. All values below have sensible
defaults for mixed short/long audio on an 8 GB VRAM GPU; override them
only if you routinely process audio longer than 2 hours or need tighter
short-audio SLAs.

| Variable                       | Default | Description                                                                                                                           |
| ------------------------------ | ------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `JOB_TIMEOUT_DEFAULT`          | `7200`  | Fallback `job_timeout` (seconds) when audio duration is unknown (e.g., YouTube URL before download).                                  |
| `JOB_TIMEOUT_FLOOR`            | `1800`  | Lower bound on the dynamic timeout so short audio still has room for model loading.                                                   |
| `JOB_TIMEOUT_AUDIO_MULTIPLIER` | `3.0`   | `estimated_timeout = duration * multiplier`. `3.0` covers large-v3 + alignment + diarization with headroom.                           |
| `JOB_TIMEOUT_MAX`              | `28800` | Absolute upper cap (8h). Also the basis for stale-job recovery.                                                                       |
| `STALE_JOB_BUFFER`             | `1800`  | Extra margin (30min) added to `JOB_TIMEOUT_MAX` before the stale-job reaper marks a processing job as dead.                           |
| `REDIS_PROCESSING_EXPIRY`      | `30600` | TTL (seconds) for Redis progress keys. See invariant below.                                                                           |
| `DIARIZE_HEARTBEAT_INTERVAL`   | `30`    | How often the diarize stage re-posts progress so the UI and stale-job reaper can tell the task is still alive. Set to `0` to disable. |

> **Invariants** (validated at startup — the service will refuse to start
> if any of these is violated):
>
> - `0 < JOB_TIMEOUT_FLOOR ≤ JOB_TIMEOUT_DEFAULT ≤ JOB_TIMEOUT_MAX`
> - `JOB_TIMEOUT_AUDIO_MULTIPLIER > 0`
> - `STALE_JOB_BUFFER ≥ 0`
> - `DIARIZE_HEARTBEAT_INTERVAL ≥ 0`
> - `REDIS_PROCESSING_EXPIRY ≥ JOB_TIMEOUT_MAX + STALE_JOB_BUFFER`
>
> When raising `JOB_TIMEOUT_MAX`, you **must** also raise
> `REDIS_PROCESSING_EXPIRY` so the Redis progress key outlives the longest
> possible job window. Otherwise the service will fail to start with a
> `ValidationError` pointing at the violated invariant.

### Optional LLM text correction

Whisper-UI can optionally post-process each transcription through a small
LLM running on [Ollama](https://ollama.com) to fix obvious typos,
homophones and punctuation errors — without rewriting wording or touching
timestamps / speaker labels. The feature is:

- **Per-job** — users tick a checkbox on the upload form. Uninterested
  users see no change.
- **Optional at deployment time** — the bundled Ollama server is a
  separate compose profile. Leaving `OLLAMA_BASE_URL` empty disables the
  feature globally and greys out the UI toggle.
- **Fail-safe** — any network, parsing or validation failure falls back
  to the original segment text. LLM correction can never turn a
  successful transcription into a failed job.

**Option A — bundled Ollama (easiest):**

```bash
# .env
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=gemma4:e2b

docker compose --profile gpu --profile llm up -d
```

The `ollama-pull` init sidecar will wait until Ollama is healthy and then
pull `OLLAMA_MODEL` on first start (~7 GB download for `gemma4:e2b`).
Check its exit status with `docker compose ps ollama-pull`; the model is
cached in the `ollama-data` volume so restarts are instant.

**Option B — external Ollama server (no profile needed):**

```bash
# .env
OLLAMA_BASE_URL=http://192.168.1.20:11434
OLLAMA_MODEL=gemma4:e2b

docker compose --profile gpu up -d
# Make sure the model is pulled on the external server yourself:
#   ollama pull gemma4:e2b
```

**Dual-GPU hosts:** the `gpu` profile pins the Whisper worker to
`WORKER_GPU_DEVICE_ID` (default 0); adding the `llm` profile also pins
the bundled Ollama container to `OLLAMA_GPU_DEVICE_ID` (default 1).
Override either variable in `.env` if your topology differs.

> **Operational breaking change (multi-GPU hosts only):** this release
> switches `worker-gpu`'s GPU reservation from `count: 1` (runtime picks
> any available GPU) to `device_ids: ["${WORKER_GPU_DEVICE_ID:-0}"]`
> (pinned to GPU 0 by default). This is required so the `llm` profile can
> reliably split Whisper and Ollama onto different devices. Impact:
>
> - **Single-GPU hosts:** no change.
> - **Multi-GPU hosts using only the `gpu` profile (no `llm`):** the
>   worker now always uses GPU 0 unless you set `WORKER_GPU_DEVICE_ID`
>   explicitly. If you previously relied on the NVIDIA runtime's
>   automatic selection (e.g. to route workloads away from a GPU already
>   held by another container), set `WORKER_GPU_DEVICE_ID` in your `.env`
>   to restore the intended placement.

**Tuning (all optional):**

| Variable                 | Default      | Description                                                                            |
| ------------------------ | ------------ | -------------------------------------------------------------------------------------- |
| `OLLAMA_BASE_URL`        | (empty)      | Empty disables the feature globally. Set to reach a bundled or external Ollama server. |
| `OLLAMA_MODEL`           | `gemma4:e2b` | Any Ollama-compatible chat model. Larger = better accuracy but more VRAM.              |
| `OLLAMA_KEEP_ALIVE`      | `30m`        | How long Ollama keeps the model loaded in VRAM between requests.                       |
| `OLLAMA_REQUEST_TIMEOUT` | `120`        | Per-request timeout in seconds.                                                        |
| `LLM_CHUNK_SIZE`         | `8`          | Segments corrected per Ollama request. Larger reduces HTTP overhead.                   |
| `LLM_CHUNK_CONTEXT`      | `2`          | Neighbor segments attached as read-only context for disambiguation.                    |
| `LLM_TEMPERATURE`        | `0.1`        | Sampling temperature. Low values keep corrections deterministic.                       |

### Optional upload retention

Long-running deployments accumulate per-job upload directories under
`data/uploads/`. Set `UPLOAD_RETENTION_DAYS` to have the web app
hourly reclaim the upload directory of any **COMPLETED** job whose
last update is older than the threshold. FAILED jobs are intentionally
preserved so the retry button keeps working — retry reuses the
original upload path and would otherwise fail at the preprocess step.

The DB row and the saved transcript (`data/outputs/<id>/result.json`)
are always kept, so viewer and export routes remain functional. The
"Download Media" button for URL jobs hides itself once the source
media is reclaimed.

```bash
# .env
UPLOAD_RETENTION_DAYS=30

docker compose --profile gpu up -d
```

| Variable                | Default | Description                                                                                                          |
| ----------------------- | ------- | -------------------------------------------------------------------------------------------------------------------- |
| `UPLOAD_RETENTION_DAYS` | `0`     | `0` disables the sweep (legacy behaviour). `>0` reclaims COMPLETED job upload dirs older than that many days hourly. |

## Local Development

```bash
# Install mise (tool manager); also pulls uv at the pinned version
mise install

# Install Python dependencies from uv.lock for a reproducible env
uv sync --extra dev

# Run tests
uv run pytest

# Run linting
uv run ruff format . && uv run ruff check .

# Start FastAPI dev server (requires Redis running)
uv run uvicorn whisper_ui.web.app:app --reload --reload-dir=src
```

> `uv.lock` is committed and is the source of truth for dependency
> versions in CI and reproducible local installs. After editing
> `pyproject.toml`, run `uv lock` to refresh it and commit both files
> together.

## Tech Stack

| Component           | Technology                                   |
| ------------------- | -------------------------------------------- |
| STT Engine          | faster-whisper large-v3 (INT8) via WhisperX  |
| Speaker Diarization | pyannote-audio (optional, requires HF token) |
| Task Queue          | RQ + Redis                                   |
| Frontend            | FastAPI + htmx + Alpine.js                   |
| Storage             | SQLite + local filesystem                    |
| Containerization    | Docker Compose (GPU / CPU profiles)          |

## Project Structure

```text
src/whisper_ui/
  core/               # Config, models, exceptions
  pipeline/           # STT processing stages
  worker/             # RQ task definitions
  storage/            # SQLite + file I/O
  export/             # SRT/VTT/TXT/JSON/DOCX exporters
  ui/                 # Shared labels
  web/                # FastAPI application
    app.py            # Application entry point
    deps.py           # Dependency injection (DB, Redis, templates)
    routes/           # Route handlers (upload, jobs, viewer)
    templates/        # Jinja2 HTML templates
    static/           # CSS and static assets
```

## Troubleshooting

### Speaker diarization not working

- Ensure `HF_TOKEN` is set in your `.env` file
- Accept **both** model agreements on HuggingFace:
  - [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1)
  - [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0)
- Diarization is optional; transcription works without it

### CPU mode fails with compute type error

- Use `COMPUTE_TYPE=int8` or `COMPUTE_TYPE=auto` for CPU
- `int8_float16` and `float16` require a GPU

### Redis connection error

- Ensure Redis is running: `docker compose ps`
- For local development, start Redis: `docker run -d -p 6379:6379 redis:7-alpine`

### Docker build is slow

- Set `PIP_INDEX_URL` in `.env` to a regional PyPI mirror

## License

[MIT](LICENSE)
