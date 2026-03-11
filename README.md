# Whisper-UI

Speech-to-text system using [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
(large-v3, INT8) with speaker diarization via
[pyannote-audio](https://github.com/pyannote/pyannote-audio),
a [Streamlit](https://streamlit.io/) web interface,
and Docker deployment (GPU / CPU).

> **Note:** The UI is in Traditional Chinese (繁體中文).

## Features

- Upload audio/video files for transcription
- Speaker diarization with pyannote speaker-diarization-3.1 (optional)
- Real-time progress tracking via Redis
- Export to SRT, VTT, TXT, JSON, DOCX
- Docker Compose deployment with GPU and CPU profiles

## Architecture

```text
+------------------+     +------------------+     +------------------+
|    Streamlit     |     |      Redis       |     |     Worker       |
|    Frontend      |<--->|   (queue+state)  |<--->|  (RQ + Pipeline) |
|   (CPU only)     |     |                  |     |  (GPU or CPU)    |
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

Open <http://localhost:8501> in your browser.

## Configuration

All settings are configured via environment variables (`.env` file):

| Variable        | Default                             | Description                                  |
| --------------- | ----------------------------------- | -------------------------------------------- |
| `WHISPER_MODEL` | `large-v3`                          | Whisper model variant (see model list below) |
| `COMPUTE_TYPE`  | `int8_float16` (GPU) / `int8` (CPU) | CTranslate2 compute type                     |
| `DEVICE`        | `cuda` (GPU) / `cpu` (CPU)          | Inference device                             |
| `BATCH_SIZE`    | `4`                                 | Transcription batch size                     |
| `LANGUAGE`      | `zh`                                | Default language code                        |
| `HF_TOKEN`      | (empty)                             | HuggingFace token for speaker diarization    |
| `PIP_INDEX_URL` | (empty)                             | Custom PyPI mirror for Docker builds         |

**Whisper models:** `tiny`, `tiny.en`, `base`, `base.en`, `small`, `small.en`, `medium`, `medium.en`, `large-v1`, `large-v2`, `large-v3`, `large-v3-turbo`

> **Tip:** For CPU deployment, use smaller models (`base`, `small`) for reasonable
> processing times. GPU deployment with `large-v3` and `int8_float16` gives the
> best accuracy-to-speed ratio.

## Local Development

```bash
# Install mise (tool manager)
mise install

# Install Python dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run linting
ruff format . && ruff check .

# Start Streamlit (requires Redis running)
streamlit run src/whisper_ui/app.py
```

## Tech Stack

| Component           | Technology                                   |
| ------------------- | -------------------------------------------- |
| STT Engine          | faster-whisper large-v3 (INT8) via WhisperX  |
| Speaker Diarization | pyannote-audio (optional, requires HF token) |
| Task Queue          | RQ + Redis                                   |
| Frontend            | Streamlit                                    |
| Storage             | SQLite + local filesystem                    |
| Containerization    | Docker Compose (GPU / CPU profiles)          |

## Project Structure

```text
src/whisper_ui/
  app.py              # Streamlit entry point
  pages/              # Streamlit multipage UI
  core/               # Config, models, exceptions
  pipeline/           # STT processing stages
  worker/             # RQ task definitions
  storage/            # SQLite + file I/O
  export/             # SRT/VTT/TXT/JSON/DOCX exporters
  ui/                 # Shared UI components
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
