"""Repeatable benchmarks for apikit.

Every number quoted in the README comes from this script. It writes
benchmarks/results.json (machine readable) and benchmarks/RESULTS.md (the table
that is pasted into the README), and it never touches the network.

  python benchmarks/run.py            # full run
  python benchmarks/run.py --quick    # fewer iterations
"""

from __future__ import annotations

import argparse
import json
import pathlib
import platform
import statistics
import sys
import threading
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from apikit.diff import diff_documents  # noqa: E402
from apikit.loader import Spec  # noqa: E402
from apikit.mock import MockOptions, create_server  # noqa: E402
from apikit.signer import canonical_string, sign_request, verify_request  # noqa: E402

EXAMPLES = ROOT / "examples"


def load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def timed(func, repeat: int) -> dict:
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        func()
        samples.append((time.perf_counter() - started) * 1000.0)
    return {
        "runs": repeat,
        "median_ms": round(statistics.median(samples), 3),
        "best_ms": round(min(samples), 3),
        "worst_ms": round(max(samples), 3),
    }


def synthetic_spec(paths: int, schemas: int, fields: int) -> dict:
    """Build a large but realistic spec in memory for the scale benchmark."""
    document = {
        "openapi": "3.0.3",
        "info": {"title": "synthetic", "version": "1.0.0"},
        "paths": {},
        "components": {"schemas": {}},
    }
    for index in range(schemas):
        properties = {"id": {"type": "integer"}}
        for field in range(fields):
            properties[f"field{field}"] = {"type": "string", "maxLength": 200}
        properties["meta"] = {"$ref": "#/components/schemas/Meta"}
        document["components"]["schemas"][f"Model{index}"] = {
            "type": "object",
            "required": ["id"],
            "properties": properties,
        }
    document["components"]["schemas"]["Meta"] = {
        "type": "object",
        "properties": {
            "createdAt": {"type": "string", "format": "date-time"},
            "updatedAt": {"type": "string", "format": "date-time"},
        },
    }
    for index in range(paths):
        document["paths"][f"/resource{index}/{{id}}"] = {
            "get": {
                "operationId": f"getResource{index}",
                "parameters": [
                    {"name": "id", "in": "path", "required": True,
                     "schema": {"type": "integer"}},
                    {"name": "limit", "in": "query", "required": False,
                     "schema": {"type": "integer", "maximum": 100}},
                ],
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": f"#/components/schemas/Model{index % schemas}"}
                            }
                        },
                    }
                },
            }
        }
    return document


def benchmark_diff(quick: bool) -> dict:
    old = load(EXAMPLES / "petstore.v1.json")
    new = load(EXAMPLES / "petstore.v2.json")
    small = timed(lambda: diff_documents(old, new), 20 if quick else 60)

    big_old = synthetic_spec(300, 40, 12)
    big_new = json.loads(json.dumps(big_old))
    big_new["info"]["version"] = "2.0.0"
    for index in range(0, 300, 3):
        big_new["paths"][f"/resource{index}/{{id}}"]["get"]["deprecated"] = True
    for index in range(0, 40, 2):
        big_new["components"]["schemas"][f"Model{index}"]["properties"]["field0"]["maxLength"] = 50
    big_new["paths"]["/brand-new"] = {
        "get": {"operationId": "newOne", "responses": {"200": {"description": "ok"}}}
    }
    large = timed(lambda: diff_documents(big_old, big_new), 3 if quick else 8)
    operations = len(Spec(big_old).operations())
    result = diff_documents(big_old, big_new)
    raw = sum(item.occurrences for item in result.findings)
    return {
        "small_spec": {"operations": len(Spec(old).operations()), **small},
        "large_spec": {
            "operations": operations,
            "schemas": 40,
            "findings": len(result.findings),
            "raw_findings": raw,
            **large,
        },
    }


def benchmark_mock(quick: bool) -> dict:
    iterations = 200 if quick else 1000
    options = MockOptions(seed=1, quiet=True)
    httpd = create_server(str(EXAMPLES / "petstore.v1.json"), port=0, options=options)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    base = f"http://{host}:{port}"
    latencies: list[float] = []
    try:
        with urllib.request.urlopen(base + "/pets", timeout=10) as response:
            response.read()
        started = time.perf_counter()
        for _ in range(iterations):
            request_started = time.perf_counter()
            with urllib.request.urlopen(base + "/pets", timeout=10) as response:
                response.read()
            latencies.append((time.perf_counter() - request_started) * 1000.0)
        elapsed = time.perf_counter() - started
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
    latencies.sort()
    return {
        "requests": iterations,
        "total_s": round(elapsed, 3),
        "requests_per_s": round(iterations / elapsed, 1),
        "p50_ms": round(latencies[len(latencies) // 2], 3),
        "p95_ms": round(latencies[int(len(latencies) * 0.95)], 3),
    }


def benchmark_signing(quick: bool) -> dict:
    """Measure whole loops, so the reported rate is operations per second."""
    count = 2000 if quick else 10000
    body = b'{"name":"Rex","tag":"dog","quantity":2}'
    secret = "bench-secret"
    headers = sign_request(secret, "POST", "/pets", "limit=5", body,
                           timestamp=1700000000, nonce="nonce-000000000001")
    signature = headers["X-Apikit-Signature"]

    def canonical_loop() -> None:
        for index in range(count):
            canonical_string("POST", "/pets", "limit=5", body, 1700000000 + index,
                             f"nonce-{index:012d}")

    def sign_loop() -> None:
        for index in range(count):
            sign_request(secret, "POST", "/pets", "limit=5", body,
                         timestamp=1700000000 + index, nonce=f"nonce-{index:012d}")

    def verify_loop() -> None:
        for _ in range(count):
            verify_request(secret, "POST", "/pets", "limit=5", body, "1700000000",
                           "nonce-000000000001", signature, now=1700000000)

    repeats = 2 if quick else 5
    canonical = timed(canonical_loop, repeats)
    signing = timed(sign_loop, repeats)
    verifying = timed(verify_loop, repeats)
    return {
        "count_per_run": count,
        "canonical_per_s": round(count * 1000.0 / canonical["median_ms"], 1),
        "sign_per_s": round(count * 1000.0 / signing["median_ms"], 1),
        "verify_per_s": round(count * 1000.0 / verifying["median_ms"], 1),
        "verify_us": round(verifying["median_ms"] * 1000.0 / count, 2),
    }


def render_markdown(results: dict) -> str:
    diff = results["diff"]
    mock = results["mock"]
    sign = results["signing"]
    lines = [
        "# Benchmarks",
        "",
        "Generated by " + "`" + "python benchmarks/run.py" + "`" + " on "
        + results["environment"]["platform"]
        + ", Python " + results["environment"]["python"]
        + ". Re-run it on your own machine before quoting these numbers.",
        "",
        "## Compatibility diff",
        "",
        "| Spec | Operations | Median | Best | Worst |",
        "| --- | ---: | ---: | ---: | ---: |",
        "| petstore example | {} | {} ms | {} ms | {} ms |".format(
            diff["small_spec"]["operations"],
            diff["small_spec"]["median_ms"],
            diff["small_spec"]["best_ms"],
            diff["small_spec"]["worst_ms"],
        ),
        "| synthetic (300 paths, 40 schemas) | {} | {} ms | {} ms | {} ms |".format(
            diff["large_spec"]["operations"],
            diff["large_spec"]["median_ms"],
            diff["large_spec"]["best_ms"],
            diff["large_spec"]["worst_ms"],
        ),
        "",
        "The synthetic run produces {} raw hits, merged into {} findings, so the".format(
            diff["large_spec"]["raw_findings"], diff["large_spec"]["findings"]
        ),
        "timing covers the whole report rather than short-circuiting on the first change.",
        "",
        "## Mock server",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        "| requests served | {} |".format(mock["requests"]),
        "| throughput | {} req/s |".format(mock["requests_per_s"]),
        "| p50 latency | {} ms |".format(mock["p50_ms"]),
        "| p95 latency | {} ms |".format(mock["p95_ms"]),
        "",
        "Loopback HTTP/1.1, one client process, full request validation enabled.",
        "",
        "## HMAC signing",
        "",
        "| Operation | Throughput |",
        "| --- | ---: |",
        "| build canonical string | {} per s |".format(sign["canonical_per_s"]),
        "| sign (canonical + HMAC-SHA256) | {} per s |".format(sign["sign_per_s"]),
        "| verify | {} per s |".format(sign["verify_per_s"]),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the apikit benchmark suite")
    parser.add_argument("--quick", action="store_true", help="fewer iterations")
    parser.add_argument("--out-dir", default=str(ROOT / "benchmarks"))
    args = parser.parse_args()

    started = time.perf_counter()
    results = {
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "machine": platform.machine(),
        },
        "diff": benchmark_diff(args.quick),
        "mock": benchmark_mock(args.quick),
        "signing": benchmark_signing(args.quick),
    }
    results["total_s"] = round(time.perf_counter() - started, 2)

    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    (out_dir / "RESULTS.md").write_text(render_markdown(results), encoding="utf-8")
    print(render_markdown(results))
    print("wrote", out_dir / "results.json", "in", results["total_s"], "s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
