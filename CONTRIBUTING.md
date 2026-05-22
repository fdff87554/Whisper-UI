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
