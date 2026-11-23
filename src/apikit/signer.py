"""HMAC-SHA256 request signing with canonical strings and replay protection.

The scheme is intentionally small enough to reimplement in another language:
see docs/SIGNING.md for the exact byte layout and the committed test vectors.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

SIGNATURE_VERSION = "v1"
DEFAULT_WINDOW = 300
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class SigningError(ValueError):
    """The request cannot be signed or the signature is malformed."""


def sha256_hex(payload: bytes) -> str:
    """Lowercase hex SHA-256 digest of *payload*."""
    return hashlib.sha256(payload).hexdigest()


def canonical_query(query: str | Mapping[str, object] | None) -> str:
    """Normalise a query string: decoded pairs, sorted, percent-encoded.

    Repeated keys keep every value. Sorting is on the decoded key then value so
    the result does not depend on the order the client happened to send.
    """
    pairs: list[tuple[str, str]] = []
    if isinstance(query, str):
        for chunk in query.split("&"):
            if not chunk:
                continue
            key, _, value = chunk.partition("=")
            pairs.append((_unquote_plus(key), _unquote_plus(value)))
    elif isinstance(query, Mapping):
        for key, value in query.items():
            if isinstance(value, (list, tuple)):
                pairs.extend((str(key), str(item)) for item in value)
            else:
                pairs.append((str(key), str(value)))
    pairs.sort()
    return "&".join(f"{quote(key, safe='')}={quote(value, safe='')}" for key, value in pairs)


def _unquote_plus(value: str) -> str:
    from urllib.parse import unquote_plus

    return unquote_plus(value)


def canonical_string(
    method: str,
    path: str,
    query: str | Mapping[str, object] | None,
    body: bytes,
    timestamp: int | str,
    nonce: str,
) -> str:
    """Build the canonical string that is actually signed.

    Six newline-separated fields, no trailing newline:
    method, path, canonical query, body sha256, timestamp, nonce.
    """
    if not method:
        raise SigningError("method is required")
    if not path.startswith("/"):
        raise SigningError(f"path must start with '/', got {path!r}")
    if not _NONCE_RE.match(str(nonce)):
        raise SigningError("nonce must be 8-128 characters of [A-Za-z0-9_-]")
    try:
        stamp = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise SigningError(f"timestamp must be an integer, got {timestamp!r}") from exc
    return "\n".join(
        [
            method.upper(),
            path,
            canonical_query(query),
            sha256_hex(body or b""),
            str(stamp),
            str(nonce),
        ]
    )


def sign_request(
    secret: str | bytes,
    method: str,
    path: str,
    query: str | Mapping[str, object] | None = None,
    body: bytes = b"",
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """Sign a request and return the headers a client must send."""
    if isinstance(secret, str):
        key = secret.encode("utf-8")
    else:
        key = secret
    if not key:
        raise SigningError("secret must not be empty")
    stamp = int(time.time()) if timestamp is None else int(timestamp)
    nonce = secrets.token_urlsafe(16) if nonce is None else nonce
    payload = canonical_string(method, path, query, body, stamp, nonce)
    digest = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    signature = f"{SIGNATURE_VERSION}={digest}"
    return {
        "X-Apikit-Timestamp": str(stamp),
        "X-Apikit-Nonce": nonce,
        "X-Apikit-Signature": signature,
        "X-Apikit-Signed-Headers": "x-apikit-timestamp;x-apikit-nonce",
    }


@dataclass
class VerifyResult:
    """Outcome of signature verification."""

    ok: bool
    reason: str = ""
    code: str = "ok"

    def __bool__(self) -> bool:
        return self.ok


class ReplayCache:
    """In-memory nonce cache with a time-to-live in seconds."""

    def __init__(self, window: int = DEFAULT_WINDOW) -> None:
        self.window = window
        self._seen: dict[str, int] = {}

    def check_and_store(self, nonce: str, timestamp: int, now: int | None = None) -> bool:
        """Return False when the nonce was already used inside the window."""
        current = int(time.time()) if now is None else int(now)
        self._purge(current)
        if nonce in self._seen:
            return False
        self._seen[nonce] = timestamp
        return True

    def _purge(self, now: int) -> None:
        expired = [key for key, stamp in self._seen.items() if abs(now - stamp) > self.window]
        for key in expired:
            del self._seen[key]

    def __len__(self) -> int:
        return len(self._seen)


def verify_request(
    secret: str | bytes,
    method: str,
    path: str,
    query: str | Mapping[str, object] | None = None,
    body: bytes = b"",
    timestamp: int | str | None = None,
    nonce: str | None = None,
    signature: str | None = None,
    window: int = DEFAULT_WINDOW,
    now: int | None = None,
    replay_cache: ReplayCache | None = None,
) -> VerifyResult:
    """Verify a signature against the canonical string.

    Every failure is reported as a reason string; no exception escapes.
    """
    if isinstance(secret, str):
        key = secret.encode("utf-8")
    else:
        key = secret
    if not key:
        return VerifyResult(False, "server secret is not configured", "no_secret")
    if not signature:
        return VerifyResult(False, "missing X-Apikit-Signature header", "missing_signature")
    if timestamp is None or nonce is None:
        return VerifyResult(False, "missing timestamp or nonce header", "missing_headers")
    prefix, _, digest = signature.partition("=")
    if prefix != SIGNATURE_VERSION or len(digest) != 64:
        return VerifyResult(
            False, f"signature must look like {SIGNATURE_VERSION}=<64 hex chars>", "malformed"
        )
    try:
        stamp = int(timestamp)
    except (TypeError, ValueError):
        return VerifyResult(False, f"timestamp {timestamp!r} is not an integer", "malformed")
    try:
        payload = canonical_string(method, path, query, body, stamp, nonce)
    except SigningError as exc:
        return VerifyResult(False, str(exc), "malformed")
    expected = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, digest.lower()):
        return VerifyResult(False, "signature does not match the request", "bad_signature")
    current = int(time.time()) if now is None else int(now)
    skew = abs(current - stamp)
    if skew > window:
        return VerifyResult(
            False,
            f"timestamp is {skew}s away from local time, outside the {window}s replay window",
            "expired",
        )
    if replay_cache is not None and not replay_cache.check_and_store(nonce, stamp, current):
        return VerifyResult(False, f"nonce {nonce!r} was already used", "replay")
    return VerifyResult(True)


def request_target(url: str) -> tuple[str, str]:
    """Split a URL into (path, query) for signing."""
    parts = urlsplit(url)
    return parts.path or "/", parts.query


def secret_from(source: str | None, env: Mapping[str, str] | None = None) -> str:
    """Resolve a secret from a literal, a file path, or the environment."""
    environment = os.environ if env is None else env
    if source is None:
        value = environment.get("APIKIT_SECRET")
        if not value:
            raise SigningError("no secret: pass --secret, --secret-file or set APIKIT_SECRET")
        return value
    return source
