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
office LAN, a VPN, or a Tailscale tailnet). It deliberately does
**not** ship the controls a public-internet service would require:

- No user authentication or session management
- No CSRF protection on state-changing routes
- No request rate limiting
- No per-user authorization on uploads, retries, or deletes

Anyone who can reach the application port can submit jobs, view
transcripts, and delete records. Do not expose Whisper-UI directly to
the public internet. If you need remote access, place it behind a
reverse proxy that adds authentication (mTLS, OIDC, basic auth, etc.)
and that itself is hardened for the public network.

## Best Practices for Deployment

- Never commit `.env` files or HuggingFace tokens to version control
- Run Docker containers with minimal privileges
- Keep dependencies updated to patch known vulnerabilities
- Restrict network access to the application port (8000) in production
