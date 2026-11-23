"""Regenerate examples/signing_vectors.json from the implementation.

Run this after changing the canonical-string layout; the test suite fails if
the committed vectors stop matching, which is exactly the point: the file is a
frozen interoperability contract you can reimplement in another language.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from apikit.signer import canonical_string, sha256_hex  # noqa: E402

SECRET = "apikit-demo-secret-2026"

CASES = [
    ("simple_get", "GET", "/pets", "", "", 1700000000, "nonce-aaaa-0001"),
    ("query_sorted", "GET", "/pets", "limit=5&tag=dog", "", 1700000000, "nonce-aaaa-0002"),
    ("query_unsorted_input", "GET", "/pets", "tag=dog&limit=5", "", 1700000000, "nonce-aaaa-0003"),
    ("json_post", "POST", "/pets", "", '{"name":"Rex","tag":"dog"}', 1700000100, "nonce-aaaa-0004"),
    ("unicode_body", "POST", "/pets", "", '{"name":"Miaou \u732b"}', 1700000200, "nonce-aaaa-0005"),
    ("empty_body_put", "PUT", "/pets/42", "", "", 1700000300, "nonce-aaaa-0006"),
    ("path_with_escape", "GET", "/files/a b", "q=1", "", 1700000400, "nonce-aaaa-0007"),
]


def main() -> int:
    import hashlib
    import hmac

    vectors = []
    for name, method, path, query, body, timestamp, nonce in CASES:
        payload = body.encode("utf-8")
        canonical = canonical_string(method, path, query, payload, timestamp, nonce)
        digest = hmac.new(
            SECRET.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        vectors.append(
            {
                "name": name,
                "method": method,
                "path": path,
                "query": query,
                "body": body,
                "timestamp": timestamp,
                "nonce": nonce,
                "canonical": canonical,
                "body_sha256": sha256_hex(payload),
                "signature": "v1=" + digest,
            }
        )
    out = {
        "algorithm": "HMAC-SHA256 over UTF-8 bytes of the canonical string",
        "secret": SECRET,
        "note": "canonical is six newline-separated fields: method, path, "
                "canonical query, body sha256, timestamp, nonce",
        "vectors": vectors,
    }
    target = ROOT / "examples" / "signing_vectors.json"
    target.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", target, "with", len(vectors), "vectors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
