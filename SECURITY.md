# Security Policy

## Supported versions

Only the latest minor release receives security fixes.

## Reporting a vulnerability

Do not open a public issue. Email the maintainer listed in `pyproject.toml`
or open a private security advisory on GitHub. We aim to respond within 72
hours.

## Threat model notes

- `apikit mock` is a development tool. It binds to `127.0.0.1` by default,
  has no authentication and must not be exposed to untrusted networks.
- `apikit mock` does not execute anything from the spec: no code generation,
  no templates, no shell calls.
- `apikit sign`/`verify` use `hmac.compare_digest` for signature comparison
  and reject timestamps outside the configured replay window. Secrets are read
  from `--secret-file` or the `APIKIT_SECRET` environment variable and are
  never written to logs.
- The tool never makes network requests; specs are read from disk.
