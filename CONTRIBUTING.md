# Contributing to Whisper-UI

Thank you for considering contributing to Whisper-UI!

## Development Setup

```bash
# Install mise (tool manager). Prefer your system package manager;
# the curl installer is a fallback for hosts without one.
curl https://mise.run | sh

# Install project runtimes (Python, Node, etc.) declared in mise.toml
mise install

# Install dependencies (dev extra pulls in frontend + worker-llm + test tools)
uv sync --extra dev

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
   uv run pytest                  # unit tests (integration excluded by default)
   uv run pytest -m integration   # integration tests (require ffmpeg on PATH)
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

See the "Project Structure" section in [README.md](README.md) for the
authoritative module layout. New code should respect the layered
dependency direction documented there (core / pipeline / worker /
storage / export / ui flow upward into web).

## Pull Request Guidelines

- Keep PRs focused on a single concern
- Include tests for new functionality
- Update documentation if the change affects user-facing behavior
- Do not commit `.env`, credentials, or other sensitive files

## Local non-Docker development

Most contributors run the app via `docker compose`, where CSS is
rebuilt automatically (production: `Dockerfile.frontend` Stage 1;
development: the `css-watcher` sidecar in `compose.dev.yml`). Skip
this section unless you need to run the app directly under `uvicorn`.

If you do run bare-metal, the compiled Tailwind output
(`src/whisper_ui/web/static/style.css`) is gitignored and Docker is
not involved, so the file must be produced locally before pages
render with the correct styles:

```bash
# One-off production-style build
mise run css

# Or rebuild on every change while developing
mise run css:watch

# Then start the app
uv run uvicorn whisper_ui.web.app:app --reload
```

Skipping the CSS build does not crash the server — FastAPI just
serves `/static/style.css` as 404 and the page renders without
styles, which is the easiest way to recognise the missing step.

## Running with Docker

```bash
# GPU
docker compose --profile gpu up -d

# CPU
docker compose --profile cpu up -d

# Development (live reload)
docker compose --profile gpu -f compose.yml -f compose.dev.yml up -d
```

## Reporting Issues

Please use [GitHub Issues](https://github.com/fdff87554/Whisper-UI/issues) to report bugs or request features.
