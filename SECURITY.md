# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do not** open a public issue
2. Email the maintainers or use [GitHub's private vulnerability reporting](https://github.com/fdff87554/Whisper-UI/security/advisories/new)
3. Include steps to reproduce and the potential impact

We will acknowledge receipt within 48 hours and aim to provide a fix or mitigation plan within 7 days.

## Scope

This policy covers the Whisper-UI application code and its Docker deployment configuration. Third-party dependencies (WhisperX, pyannote-audio, Streamlit, etc.) have their own security policies.

## Best Practices for Deployment

- Never commit `.env` files or HuggingFace tokens to version control
- Run Docker containers with minimal privileges
- Keep dependencies updated to patch known vulnerabilities
- Restrict network access to the Streamlit port (8501) in production
