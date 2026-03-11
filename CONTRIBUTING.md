# Contributing to Whisper-UI

Thank you for considering contributing to Whisper-UI!

## Development Setup

```bash
# Install mise (tool manager)
curl https://mise.run | sh

# Install project tools and Python
mise install

# Install dependencies (dev includes frontend + test tools)
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install
```

## Development Workflow

1. Create a branch from `main` for your changes
2. Make atomic commits with clear messages
3. Run formatter and linter before committing:

   ```bash
   pre-commit run --all-files
   ```

4. Run tests:

   ```bash
   pytest
   ```

5. Submit a pull request with a clear description of:
   - What the change does and why
   - Impact on existing functionality
   - Any breaking changes

## Code Style

- **Python**: Formatted with `ruff format`, linted with `ruff check` (config in `pyproject.toml`)
- **Shell**: Formatted with `shfmt`, linted with `shellcheck`
- **YAML**: Formatted with `yamlfmt`
- **Markdown**: Formatted with `prettier`, linted with `markdownlint-cli2`

All checks are enforced via pre-commit hooks.

## Architecture

```text
src/whisper_ui/
  core/       # Config, models, exceptions, shared constants
  pipeline/   # STT processing stages (preprocess -> transcribe -> align -> diarize -> postprocess)
  worker/     # RQ task queue definitions
  storage/    # SQLite database + file I/O
  export/     # SRT/VTT/TXT/JSON/DOCX exporters
  ui/         # Streamlit UI components and labels
  pages/      # Streamlit multipage views
```

**Layer dependencies** (each layer may only import from layers above it):

```text
core <- pipeline <- worker <- ui/pages
core <- storage  <- worker <- ui/pages
core <- export              <- ui/pages
```

## Pull Request Guidelines

- Keep PRs focused on a single concern
- Include tests for new functionality
- Update documentation if the change affects user-facing behavior
- Do not commit `.env`, credentials, or other sensitive files

## Running with Docker

```bash
# GPU
docker compose --profile gpu up -d

# CPU
docker compose --profile cpu up -d

# Development (live reload)
docker compose --profile gpu -f compose.yaml -f compose.dev.yaml up -d
```

## Reporting Issues

Please use [GitHub Issues](https://github.com/fdff87554/Whisper-UI/issues) to report bugs or request features.
