"""Tests for HMAC request signing, including the committed test vectors."""

from __future__ import annotations

import json
import pathlib

import pytest

from apikit.signer import (
    ReplayCache,
    SigningError,
    canonical_query,
    canonical_string,
    request_target,
    sign_request,
    verify_request,
)

VECTORS = pathlib.Path(__file__).resolve().parents[1] / "examples" / "signing_vectors.json"


def load_vectors() -> dict:
    return json.loads(VECTORS.read_text(encoding="utf-8"))


def test_committed_vectors_verify():
    data = load_vectors()
    assert len(data["vectors"]) >= 4
    for vector in data["vectors"]:
        result = verify_request(
            data["secret"],
            method=vector["method"],
            path=vector["path"],
            query=vector["query"],
            body=vector["body"].encode("utf-8"),
            timestamp=vector["timestamp"],
            nonce=vector["nonce"],
            signature=vector["signature"],
            now=vector["timestamp"],
        )
        assert result.ok, vector["name"] + ": " + result.reason


def test_committed_vectors_match_canonical_string():
    data = load_vectors()
    for vector in data["vectors"]:
        built = canonical_string(
            vector["method"],
            vector["path"],
            vector["query"],
            vector["body"].encode("utf-8"),
            vector["timestamp"],
            vector["nonce"],
        )
        assert built == vector["canonical"]


def test_signature_shape():
    headers = sign_request("secret", "GET", "/pets", "limit=5", b"", timestamp=1700000000,
                           nonce="abcdefghijkl")
    assert headers["X-Apikit-Signature"].startswith("v1=")
    assert len(headers["X-Apikit-Signature"]) == 3 + 64
    assert headers["X-Apikit-Timestamp"] == "1700000000"
    assert headers["X-Apikit-Nonce"] == "abcdefghijkl"


def test_sign_is_deterministic_for_fixed_timestamp_and_nonce():
    first = sign_request("secret", "GET", "/pets", "", b"", timestamp=1700000000,
                         nonce="abcdefghijkl")
    second = sign_request("secret", "GET", "/pets", "", b"", timestamp=1700000000,
                          nonce="abcdefghijkl")
    assert first == second


def test_query_order_does_not_change_the_signature():
    first = sign_request("s", "GET", "/pets", "a=1&b=2", b"", timestamp=1,
                         nonce="abcdefghijkl")
    second = sign_request("s", "GET", "/pets", "b=2&a=1", b"", timestamp=1,
                          nonce="abcdefghijkl")
    assert first["X-Apikit-Signature"] == second["X-Apikit-Signature"]


def test_repeated_query_keys_are_preserved():
    assert canonical_query("tag=dog&tag=cat") == "tag=cat&tag=dog"


def test_body_change_invalidates_the_signature():
    headers = sign_request("s", "POST", "/pets", "", b'{"a":1}', timestamp=1700000000,
                           nonce="abcdefghijkl")
    result = verify_request(
        "s", "POST", "/pets", "", b'{"a":2}', "1700000000", "abcdefghijkl",
        headers["X-Apikit-Signature"], now=1700000000,
    )
    assert not result.ok
    assert result.code == "bad_signature"


def test_old_timestamp_is_rejected():
    headers = sign_request("s", "GET", "/pets", "", b"", timestamp=1700000000,
                           nonce="abcdefghijkl")
    result = verify_request(
        "s", "GET", "/pets", "", b"", "1700000000", "abcdefghijkl",
        headers["X-Apikit-Signature"], now=1700009999,
    )
    assert not result.ok
    assert result.code == "expired"


def test_nonce_replay_is_rejected():
    cache = ReplayCache(window=300)
    headers = sign_request("s", "GET", "/pets", "", b"", timestamp=1700000000,
                           nonce="abcdefghijkl")
    first = verify_request(
        "s", "GET", "/pets", "", b"", "1700000000", "abcdefghijkl",
        headers["X-Apikit-Signature"], now=1700000000, replay_cache=cache,
    )
    second = verify_request(
        "s", "GET", "/pets", "", b"", "1700000000", "abcdefghijkl",
        headers["X-Apikit-Signature"], now=1700000000, replay_cache=cache,
    )
    assert first.ok
    assert not second.ok
    assert second.code == "replay"


def test_missing_headers_are_reported():
    result = verify_request("s", "GET", "/pets")
    assert not result.ok
    assert result.code == "missing_signature"


def test_malformed_signature_is_reported():
    result = verify_request("s", "GET", "/pets", "", b"", 1, "abcdefghijkl", "deadbeef", now=1)
    assert not result.ok
    assert result.code == "malformed"


def test_canonical_string_rejects_bad_input():
    with pytest.raises(SigningError):
        canonical_string("GET", "pets", "", b"", 1, "abcdefghijkl")
    with pytest.raises(SigningError):
        canonical_string("GET", "/pets", "", b"", 1, "short")
    with pytest.raises(SigningError):
        canonical_string("GET", "/pets", "", b"", "not-a-time", "abcdefghijkl")


def test_signing_rejects_empty_secret():
    with pytest.raises(SigningError):
        sign_request("", "GET", "/pets")


def test_request_target_splits_url():
    assert request_target("https://api.test/v1/pets?tag=dog&limit=2") == (
        "/v1/pets",
        "tag=dog&limit=2",
    )
    assert request_target("https://api.test") == ("/", "")


def test_empty_secret_is_rejected_on_verify():
    result = verify_request("", "GET", "/pets", "", b"", 1, "abcdefghijkl", "v1=" + "0" * 64)
    assert result.code == "no_secret"
