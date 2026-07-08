# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do not** open a public issue
2. Email the maintainers or use [GitHub's private vulnerability reporting](https://github.com/fdff87554/Whisper-UI/security/advisories/new)
3. Include steps to reproduce and the potential impact

We will acknowledge receipt within 48 hours and aim to provide a fix or mitigation plan within 7 days.

## Scope

This policy covers the Whisper-UI application code and its Docker deployment configuration. Third-party dependencies (WhisperX, pyannote-audio, FastAPI, etc.) have their own security policies.

## Intended Deployment Model

Whisper-UI is designed for **internal-network deployment** (e.g. an
office LAN, a VPN, or a Tailscale tailnet). It is not hardened for
direct exposure on the public internet, but it does ship a baseline of
application-level controls so multiple users can safely share one
deployment:

- **Authentication**: session-cookie login with argon2id password hashing
  (`web/auth.py`, `storage/users_repo.py`). The first visitor on a fresh
  database is forced through a one-shot bootstrap registration that creates
  the initial admin (`web/app.py`, `web/auth.py`).
- **CSRF protection**: state-changing requests are rejected unless `Origin`
  (or `Referer` as fallback) matches the request `Host` (`web/auth.py`).
- **Rate limiting**: per-username and per-IP login throttling, plus per-IP
  throttling of open (non-bootstrap) registration to bound automated account
  creation and the username-taken enumeration oracle, backed by Redis
  (`web/rate_limit.py`, `web/routes/auth_routes.py`).
- **Per-user authorization**: jobs are scoped to their `owner_id`; non-admin
  users only see their own transcripts, downloads, and delete actions
  (`web/routes/jobs.py`, `web/routes/viewer.py`).
- **Session revocation**: each user row carries a `session_version` that is
  bumped on password reset or account deactivation, invalidating any
  outstanding session cookie.
- **Defense-in-depth headers**: `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy` set on every response (`web/app.py`
  `SecurityHeadersMiddleware`). CSP / HSTS are intentionally not set; CSP
  is left to deployment-time configuration because templates load
  htmx / Alpine.js from jsDelivr, and HSTS is owned by the upstream TLS
  proxy.
- **Upload hardening**: filenames stripped to basename, sizes capped via
  streamed reads, allowed extensions enumerated, output paths derived from
  job IDs (`web/routes/upload.py`, `storage/filestore.py`).
- **URL ingest whitelist**: link downloads accept only YouTube, Google Drive,
  and Twitter/X URLs from fixed host sets; each URL is canonicalised from the
  extracted ID before download (`core/url_validation.py`). The YouTube and
  Twitter/X paths additionally pin yt-dlp's `allowed_extractors`, so a crafted
  link can never fall back to the generic extractor and reach an arbitrary
  (e.g. internal) host (`pipeline/download.py`). Optional X login cookies are an
  operator-mounted file, never user input, so they do not widen this surface.
- **Template autoescape**: enabled (FastAPI / Jinja2 default).
- **Error surface**: unhandled exceptions return a generic 500; tracebacks
  go to the operator log only (`web/app.py`).
- **Log hygiene**: failed-login and registration events mask the submitted
  username (`mask_username`) since it is attacker-controlled and occasionally
  credential-adjacent, and the Redis URL is redacted before logging so a
  configured password never reaches the logs; authenticated users are still
  recorded in full via the structured access log.

Even with the above, the application is **not** designed to face the
open internet directly. Place it behind a reverse proxy that terminates
TLS, rewrites `Host`, and either provides additional access control or
restricts the network the application is reachable from.

## Best Practices for Deployment

- Set a stable `SESSION_SECRET` (`openssl rand -hex 32`). An empty value
  generates an ephemeral random secret per process and logs a warning;
  every restart will invalidate all sessions.
- Set `SESSION_HTTPS_ONLY=true` when running behind TLS so the session
  cookie is marked `Secure`.
- If you set `TRUST_PROXY_HEADERS=true`, set `TRUSTED_PROXY_COUNT` to the
  number of reverse proxies in front of the app (default 1). The client IP
  is taken as the Nth entry from the **right** of `X-Forwarded-For` (the hops
  your own proxies appended); anything further left is treated as
  client-controlled and ignored. The reverse proxy must still append (not
  trust) the inbound header. Otherwise a hostile client can spoof its IP and
  host to defeat rate-limit and CSRF checks. See the README "Multi-user
  authentication" section for full operator guidance.
- Set `METRICS_TOKEN` to require `Authorization: Bearer <token>` on `/metrics`
  when the box is reachable publicly. `/metrics` is open by default (it exposes
  only counts/depths, no PII); a token or a reverse-proxy block hardens it.
- Set `REDIS_PASSWORD` before exposing the bundled Redis beyond the local
  Docker network. Compose always passes `--requirepass "${REDIS_PASSWORD:-}"`,
  so an unset password leaves Redis open (empty password = no auth).
- Never commit `.env` files or HuggingFace tokens to version control.
- Run Docker containers with minimal privileges.
- Keep dependencies updated; the project ships a GitHub Dependabot
  config (`.github/dependabot.yml`) and CI runs dependency checks.
- Restrict network access to the published port (default 8080) so reach is
  limited to the intended audience.

## Known Accepted Trade-offs

These are deliberate design choices for the internal-network deployment model,
documented here so they are explicit rather than surprises:

- **Logout is not server-side session revocation.** `/logout` clears the
  browser's signed session cookie but does not bump `session_version`, so a
  cookie value captured before logout (e.g. on a shared machine, or via a
  plaintext channel when `SESSION_HTTPS_ONLY` is off) stays valid until its
  14-day `max_age` expires. Admin password reset / account deactivation _do_
  bump `session_version` and revoke immediately. On shared machines, close the
  browser session; for immediate revocation, an admin can reset the password.
- **Session cookie `Secure` defaults off.** The bundled compose profiles serve
  plain HTTP, so `SESSION_HTTPS_ONLY` defaults to `false`. Set it to `true`
  behind TLS so the cookie is only sent over HTTPS.
- **`SESSION_SECRET` unset does not fail startup.** An empty value falls back to
  an ephemeral per-process secret (logged as a warning) so dev/compose still
  boots; production must set a stable value or every restart logs everyone out.
- **Front-end JS loads from jsDelivr (with SRI).** htmx and Alpine.js are loaded
  from the jsDelivr CDN pinned by Subresource Integrity hash, so a tampered CDN
  asset is rejected by the browser. A fully air-gapped deployment should vendor
  these locally and tighten the CSP `script-src` accordingly.
