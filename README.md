# Whisper-UI

Speech-to-text system using [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
(large-v3, INT8) with speaker diarization via
[pyannote-audio](https://github.com/pyannote/pyannote-audio),
a [Streamlit](https://streamlit.io/) web interface,
and Docker GPU deployment.

## Features

- Upload audio/video files for transcription
- Speaker diarization with pyannote speaker-diarization-3.1
- Real-time progress tracking via Redis
- Export to SRT, VTT, TXT, JSON, DOCX
- Docker Compose deployment with NVIDIA GPU support

## Architecture

```text
+------------------+     +------------------+     +------------------+
|    Streamlit     |     |      Redis       |     |   GPU Worker     |
|    Frontend      |<--->|   (queue+state)  |<--->|  (RQ + Pipeline) |
|   (CPU only)     |     |                  |     |  (NVIDIA GPU)    |
+------------------+     +------------------+     +------------------+
        |                                                  |
        +------ Shared Volume: app-data (uploads/outputs/db) ------+
```

## Quick Start

### Prerequisites

- Docker and Docker Compose
- NVIDIA GPU with 8GB+ VRAM
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- HuggingFace token (for speaker diarization)

### Setup

1. Copy environment file and configure:

   ```bash
   cp .env.example .env
   # Edit .env and set HF_TOKEN
   ```

2. Accept pyannote model agreements:

   - <https://huggingface.co/pyannote/speaker-diarization-3.1>
   - <https://huggingface.co/pyannote/segmentation-3.0>

3. Start all services:

   ```bash
   docker compose up -d
   ```

4. Open <http://localhost:8501> in your browser.

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

| Component           | Technology                                  |
| ------------------- | ------------------------------------------- |
| STT Engine          | faster-whisper large-v3 (INT8) via WhisperX |
| Speaker Diarization | pyannote-audio 3.3.2                        |
| Task Queue          | RQ + Redis                                  |
| Frontend            | Streamlit                                   |
| Storage             | SQLite + local filesystem                   |
| Containerization    | Docker Compose + NVIDIA Container Toolkit   |

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
