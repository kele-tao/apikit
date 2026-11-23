# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-23

### Added

- `apikit diff` - OpenAPI 3 breaking-change detector with 30 documented rules,
  severity levels, JSON-pointer locations, one-line fixes and a suggested
  semantic version bump.
- `apikit mock` - deterministic mock server built on `http.server` with
  schema-driven example generation, request validation, RFC 7807 errors,
  latency/error injection, CORS and a colourised access log.
- `apikit sign` / `apikit verify` - HMAC-SHA256 request signing over a
  canonical request string with replay-window and nonce-replay protection.
- `apikit examples` - curl, HTTPie and pure-Python snippets for every
  operation in a spec.
- Library API: `from apikit import diff_specs, generate_example, sign_request`.
- 30-rule reference table, runnable petstore v1/v2 example, committed signing
  test vectors and a benchmark suite.
