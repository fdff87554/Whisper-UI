# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-05-16

### BREAKING

- Removed the legacy `whisper_ui.worker.tasks.process_transcription`
  entry point. The DAG dispatcher in
  `whisper_ui.worker.pipeline_dispatcher.enqueue_pipeline` has been
  the only path the web routes use for several releases; the legacy
  entry survived only to keep already-queued sub-jobs running across
  upgrades. Drain the RQ queue (or `FLUSHDB` it) before upgrading.
  See README "Upgrading from v1.x to v2.0" for the procedure.
- Removed the four hand-tuned `STAGE_WEIGHTS_*` dicts and the
  orchestrator's re-exports. Callers needing pipeline-shape-aware
  bands now invoke
  `whisper_ui.pipeline.progress_bands.build_stage_weights(
has_download=..., has_llm=...)`. Exact percentages drift a couple
  of points from the legacy hand-tuned values.
- `pyproject.toml` now requires Python `>=3.13` (the Docker images,
  `mise.toml`, and ruff `target-version` were already pinned there).

### Added

- `SecurityHeadersMiddleware` sets `X-Content-Type-Options`,
  `X-Frame-Options`, and `Referrer-Policy` on every response for
  defense in depth, even though the deployment model is
  internal-network only.
- Global FastAPI exception handler returns a generic `Internal Server
Error` JSON body while logging the full traceback, so an
  unanticipated 500 no longer echoes file paths or partial SQL to the
  client.
- Opt-in upload retention loop: setting `UPLOAD_RETENTION_DAYS > 0`
  has the web app hourly reclaim the upload directory of COMPLETED
  jobs older than the threshold while keeping the DB row and
  `result.json` for viewer access. FAILED jobs are preserved so the
  retry button keeps working. The variable is wired through
  `compose.yml`'s frontend service and documented in the README
  "Optional upload retention" section.
- `uv.lock` is now committed and consumed by both CI jobs (`uv sync
--frozen`). Dependabot watches `uv`, GitHub Actions, and the
  Dockerfiles for upstream updates.
- CI gained a dedicated integration-tests job (installs ffmpeg) and a
  `pip-audit` step against the locked requirements. The audit scans
  the dev extra (which transitively pulls in `frontend` + `worker-llm`,
  covering fastapi / python-docx / httpx); the heavy worker ML extras
  are monitored by Dependabot instead to avoid persistent alerts from
  upstream-unpatched CVEs.

### Changed

- `worker.runtime.build_worker_runtime` accepts a `generation` kwarg
  and stamps it on the bundled reporter, replacing the two
  `_build_generation_aware_reporter` helpers that previously
  re-instantiated the reporter in stage tasks and finalize callbacks.
- Callback helpers (`mark_failed`, `cancel_remaining_subjobs`,
  `format_failure_message`, `extract_meta_generation`,
  `is_stale_callback`) moved from `worker.pipeline_dispatcher` into a
  new `worker.pipeline_callbacks` module. The `rq.Callback` entry
  points themselves stay at their existing dotted paths.
- `worker.progress` shares the terminal-write generation gate between
  `complete()` and `fail()` via an interpolated `_LUA_TERMINAL_GENERATION_GATE`
  snippet, closing the duplication that PR #39 Round 4 shipped one
  drift bug from.
- `is_llm_active(job, settings)` and `cleanup_preprocessed_audio` are
  now single helpers in `worker.runtime` instead of being duplicated
  across the dispatcher and the (removed) legacy task path.
- Generation / context TTLs collapse into one
  `core.constants.PIPELINE_STATE_TTL_SECONDS` constant; both worker
  modules import from it.
- Settings now uses `extra="ignore"` so worker-only env vars
  (`WORKER_GPU_QUEUES`, `WORKER_IO_QUEUES`) coming from the same
  `.env` no longer block the web tier from starting.
- The upload-retention sweep now offloads its SQLite query and
  per-job `shutil.rmtree` calls through `asyncio.to_thread` and caps
  each pass at 200 jobs, so a long-deferred sweep cannot stall the
  FastAPI event loop or starve incoming requests.
- README now points at `uv sync --extra dev` / `uv run` for local
  development and warns that production deployments must set
  `REDIS_PASSWORD`. The quick-start URL was corrected to match the
  compose port mapping (`localhost:8080`).
- Dependency upper bounds tightened: `faster-whisper<2`,
  `transformers<5`, `redis<7`. `taplo` versions aligned between
  `mise.toml` and `.pre-commit-config.yaml` at `0.9.3` (upstream
  pre-commit wrapper has not yet published a `0.10.x` tag).

### Removed

- `src/whisper_ui/worker/tasks.py` (legacy monolithic worker).
- `tests/integration/test_worker.py` — its assertions (timeout
  classification, missing-job handling) are now covered by unit tests
  in `test_pipeline_dispatcher.py` and `test_stage_tasks.py`.

[2.0.0]: https://github.com/fdff87554/Whisper-UI/releases/tag/v2.0.0
