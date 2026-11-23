"""A deterministic mock server for OpenAPI 3 documents.

Built on http.server so it has no dependencies. The server is deterministic:
the response body for an operation depends only on the seed and the operation
id, never on the request order, except when --stateful or --error-rate change
behaviour by design.
"""

from __future__ import annotations

import json
import random
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, TextIO
from urllib.parse import parse_qs, unquote, urlsplit

from .generate import GenerationError, generate_example
from .loader import Spec
from .pointer import SpecError
from .validate import Issue, coerce_param, validate_value

_STATUS_COLORS = {
    "2": "\x1b[32m",
    "3": "\x1b[36m",
    "4": "\x1b[33m",
    "5": "\x1b[31m",
}
_RESET = "\x1b[0m"


@dataclass
class MockOptions:
    """Everything the handler needs to know about how to behave."""

    seed: int = 0
    latency_ms: int = 0
    error_rate: float = 0.0
    cors: bool = False
    stateful: bool = False
    color: bool = False
    quiet: bool = False
    max_examples: int = 1
    log_stream: TextIO | None = None


@dataclass
class Route:
    """One compiled path template with its operations."""

    template: str
    pattern: re.Pattern[str]
    operations: dict[str, dict[str, Any]] = field(default_factory=dict)

    def match(self, path: str) -> dict[str, str] | None:
        found = self.pattern.fullmatch(path)
        if not found:
            return None
        return {key: unquote(value) for key, value in found.groupdict().items()}


def compile_path(template: str) -> re.Pattern[str]:
    """Turn an OpenAPI path template into an anchored regex."""
    pieces: list[str] = []
    index = 0
    for found in re.finditer(r"\{([^}/]+)\}", template):
        pieces.append(re.escape(template[index : found.start()]))
        pieces.append(f"(?P<{found.group(1).replace('-', '_')}>[^/]+)")
        index = found.end()
    pieces.append(re.escape(template[index:]))
    return re.compile("^" + "".join(pieces) + "$")


def build_routes(spec: Spec) -> list[Route]:
    """Compile every path template in the spec into a :class:Route."""
    routes: list[Route] = []
    for path, item in spec.paths().items():
        route = Route(template=path, pattern=compile_path(path))
        for method in ("get", "put", "post", "delete", "options", "head", "patch"):
            operation = item.get(method)
            if isinstance(operation, dict):
                merged = dict(operation)
                merged["parameters"] = _merge_parameters(
                    item.get("parameters"), operation.get("parameters")
                )
                route.operations[method] = spec.deref(merged)
        routes.append(route)
    return routes


def _merge_parameters(shared: Any, own: Any) -> list[Any]:
    merged: list[Any] = []
    seen: set[tuple[str, str]] = set()
    for parameter in list(own or []) + list(shared or []):
        if not isinstance(parameter, dict):
            continue
        key = (str(parameter.get("name")), str(parameter.get("in")))
        if key in seen:
            continue
        seen.add(key)
        merged.append(parameter)
    return merged


def problem(status: int, title: str, detail: str, issues: list[Issue] | None = None) -> bytes:
    """Build an RFC 7807 problem+json document."""
    payload: dict[str, Any] = {
        "type": "https://apikit.dev/problems/" + title.lower().replace(" ", "-"),
        "title": title,
        "status": status,
        "detail": detail,
    }
    if issues:
        payload["errors"] = [{"pointer": item.path, "detail": item.message} for item in issues]
    return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")


class MockState:
    """Shared server state: options, routes and the stateful store."""

    def __init__(self, spec: Spec, options: MockOptions) -> None:
        self.spec = spec
        self.options = options
        self.routes = build_routes(spec)
        self.store: dict[str, list[dict[str, Any]]] = {}
        self.records: dict[Any, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.counter = 0
        self.error_rng = random.Random(options.seed)
        self.log_stream = options.log_stream or sys.stderr

    def next_request_id(self) -> int:
        with self.lock:
            self.counter += 1
            return self.counter

    def find(self, path: str, method: str) -> tuple[Route, dict[str, Any], dict[str, str]] | None:
        key = method.lower()
        for route in self.routes:
            params = route.match(path)
            if params is None:
                continue
            operation = route.operations.get(key)
            if operation is None:
                continue
            return route, operation, params
        return None

    def allowed_methods(self, path: str) -> list[str]:
        allowed: list[str] = []
        for route in self.routes:
            if route.match(path) is not None:
                allowed.extend(method.upper() for method in route.operations)
        return sorted(set(allowed))

    def log(self, method: str, path: str, status: int, micros: float) -> None:
        if self.options.quiet:
            return
        stamp = time.strftime("%H:%M:%S")
        code = str(status)
        colour = _STATUS_COLORS.get(code[0], "") if self.options.color else ""
        end = _RESET if self.options.color else ""
        if self.options.color:
            method_text = f"\x1b[1m{method:<6}\x1b[0m"
        else:
            method_text = f"{method:<6}"
        self.log_stream.write(
            f"{stamp} {method_text} {path:<44} {colour}{code}{end} {micros / 1000:.1f}ms\n"
        )
        self.log_stream.flush()


def build_handler(state: MockState) -> type[BaseHTTPRequestHandler]:
    """Create the request handler class bound to *state*."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "apikit-mock/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            return

        # -- verbs ------------------------------------------------------
        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PUT(self) -> None:
            self._dispatch("PUT")

        def do_PATCH(self) -> None:
            self._dispatch("PATCH")

        def do_DELETE(self) -> None:
            self._dispatch("DELETE")

        def do_HEAD(self) -> None:
            self._dispatch("HEAD")

        def do_OPTIONS(self) -> None:
            if state.options.cors:
                self._send(204, b"", "text/plain; charset=utf-8", extra_headers=self._cors())
                return
            allowed = state.allowed_methods(urlsplit(self.path).path)
            if allowed:
                self._send(
                    204,
                    b"",
                    "text/plain; charset=utf-8",
                    extra_headers={"Allow": ", ".join(allowed + ["OPTIONS"])},
                )
            else:
                self._problem(404, "Not Found", f"no route matches {urlsplit(self.path).path}")

        # -- core -------------------------------------------------------
        def _dispatch(self, method: str) -> None:
            started = time.perf_counter()
            parts = urlsplit(self.path)
            path = parts.path or "/"
            request_id = state.next_request_id()
            try:
                if state.options.latency_ms:
                    time.sleep(state.options.latency_ms / 1000.0)
                status = self._handle(method, path, parts.query, request_id)
            except BrokenPipeError:
                return
            except Exception as exc:  # noqa: BLE001 - the mock must never crash the server
                status = 500
                self._problem(500, "Internal Server Error", f"mock server failure: {exc}")
            elapsed = (time.perf_counter() - started) * 1000
            state.log(method, self.path, status, elapsed)

        def _handle(self, method: str, path: str, query: str, request_id: int) -> int:
            found = state.find(path, method)
            if found is None:
                allowed = state.allowed_methods(path)
                if allowed:
                    self._problem(
                        405,
                        "Method Not Allowed",
                        f"{method} is not defined for {path}; allowed: {', '.join(allowed)}",
                        extra_headers={"Allow": ", ".join(allowed)},
                    )
                    return 405
                self._problem(404, "Not Found", f"no route in the spec matches {path}")
                return 404

            route, operation, path_params = found
            query_params = parse_qs(query, keep_blank_values=True)
            issues = self._validate_params(operation, path_params, query_params)
            if issues:
                self._problem(
                    422,
                    "Unprocessable Entity",
                    f"request does not match the contract for {method} {route.template}",
                    issues=issues,
                )
                return 422

            if state.options.error_rate > 0 and state.error_rng.random() < state.options.error_rate:
                self._problem(
                    503,
                    "Service Unavailable",
                    "injected failure: --error-rate is enabled for this mock server",
                )
                return 503

            body, decoded = self._read_body(operation)
            if not decoded:
                return 400
            if body is not None:
                schema = _request_schema(operation)
                if schema is not None:
                    body_issues = validate_value(body, schema, "body")
                    if body_issues:
                        self._problem(
                            422,
                            "Unprocessable Entity",
                            "request body does not match the contract",
                            issues=body_issues,
                        )
                        return 422

            if state.options.stateful and method == "POST" and isinstance(body, dict):
                with state.lock:
                    bucket = state.store.setdefault(route.template, [])
                    record = dict(body)
                    record.setdefault("id", len(bucket) + 1)
                    bucket.append(record)
                    state.records[str(record["id"])] = record
                self._send_json(201, record)
                return 201

            if state.options.stateful and method == "GET" and path_params:
                wanted = next(iter(path_params.values()))
                with state.lock:
                    record = state.records.get(str(wanted))
                if record is not None:
                    self._send_json(200, record)
                    return 200

            status, payload = self._response_for(operation, request_id)
            if payload is None:
                self._send(status, b"", "application/json; charset=utf-8")
            else:
                self._send(status, payload, "application/json; charset=utf-8")
            return status

        # -- request parsing -------------------------------------------
        def _read_body(self, operation: dict[str, Any]) -> tuple[Any, bool]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return None, True
            raw = self.rfile.read(length)
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            try:
                return json.loads(raw.decode("utf-8")), True
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._problem(
                    400,
                    "Bad Request",
                    f"body is not valid JSON ({content_type or 'no content type'}): {exc}",
                )
                return None, False

        def _validate_params(
            self,
            operation: dict[str, Any],
            path_params: dict[str, str],
            query_params: dict[str, list[str]],
        ) -> list[Issue]:
            issues: list[Issue] = []
            parameters = operation.get("parameters")
            if not isinstance(parameters, list):
                return issues
            for parameter in parameters:
                if not isinstance(parameter, dict):
                    continue
                name = parameter.get("name")
                where = parameter.get("in")
                if not isinstance(name, str) or where not in ("path", "query", "header"):
                    continue
                schema = parameter.get("schema") or {}
                if where == "path":
                    raw = path_params.get(name.replace("-", "_"))
                    if raw is None:
                        issues.append(Issue(f"path.{name}", "missing path parameter"))
                        continue
                    value, issue = coerce_param(raw, schema)
                    if issue:
                        issues.append(Issue(f"path.{name}", issue.message))
                        continue
                    for item in validate_value(value, schema, f"path.{name}"):
                        issues.append(item)
                elif where == "query":
                    values = query_params.get(name)
                    if not values:
                        if parameter.get("required"):
                            message = "required query parameter is missing"
                            issues.append(Issue(f"query.{name}", message))
                        continue
                    value, issue = coerce_param(values[0], schema)
                    if issue:
                        issues.append(Issue(f"query.{name}", issue.message))
                        continue
                    for item in validate_value(value, schema, f"query.{name}"):
                        issues.append(item)
            return issues

        # -- response building -----------------------------------------
        def _response_for(
            self, operation: dict[str, Any], request_id: int
        ) -> tuple[int, bytes | None]:
            responses = operation.get("responses")
            if not isinstance(responses, dict) or not responses:
                self._problem(500, "Internal Server Error", "operation defines no responses")
                return 500, None
            status_text = _pick_status(responses)
            status = int(status_text) if status_text.isdigit() else 200
            response = responses[status_text]
            if not isinstance(response, dict):
                return status, None
            content = response.get("content")
            if not isinstance(content, dict) or not content:
                return status, b""
            media = "application/json" if "application/json" in content else next(iter(content))
            media_object = content[media]
            schema = media_object.get("schema") if isinstance(media_object, dict) else None
            if schema is None:
                return status, b"{}"
            example = media_object.get("example") if isinstance(media_object, dict) else None
            if example is not None:
                return status, json.dumps(example, indent=2, ensure_ascii=False).encode("utf-8")
            try:
                value = generate_example(
                    schema,
                    seed=state.options.seed,
                    name=f"{operation.get('operationId') or 'operation'}/{request_id}",
                )
            except GenerationError as exc:
                self._problem(500, "Internal Server Error", f"cannot generate example: {exc}")
                return 500, None
            return status, json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8")

        # -- transport -------------------------------------------------
        def _cors(self) -> dict[str, str]:
            return {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Apikit-Signature",
            }

        def _send_json(self, status: int, value: Any) -> None:
            payload = json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8")
            self._send(status, payload, "application/json; charset=utf-8")

        def _problem(
            self,
            status: int,
            title: str,
            detail: str,
            issues: list[Issue] | None = None,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            self._send(
                status,
                problem(status, title, detail, issues),
                "application/problem+json; charset=utf-8",
                extra_headers=extra_headers,
            )

        def _send(
            self,
            status: int,
            payload: bytes,
            content_type: str,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("X-Apikit-Mock", "true")
            if state.options.cors:
                for key, value in self._cors().items():
                    self.send_header(key, value)
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD" and payload:
                self.wfile.write(payload)

    return Handler


def _pick_status(responses: dict[str, Any]) -> str:
    for code in ("200", "201", "202", "204", "default"):
        if code in responses:
            return code
    for code in sorted(responses, key=str):
        if str(code).startswith("2"):
            return str(code)
    return sorted(responses, key=str)[0]


def _request_schema(operation: dict[str, Any]) -> Any:
    body = operation.get("requestBody")
    if not isinstance(body, dict):
        return None
    content = body.get("content")
    if not isinstance(content, dict):
        return None
    media = content.get("application/json") or next(iter(content.values()), None)
    if isinstance(media, dict):
        return media.get("schema")
    return None


def create_server(
    spec_path: str,
    host: str = "127.0.0.1",
    port: int = 0,
    options: MockOptions | None = None,
) -> ThreadingHTTPServer:
    """Build (but do not start) a mock server for the spec at *spec_path*."""
    spec = Spec.from_file(spec_path)
    state = MockState(spec, options or MockOptions())
    handler = build_handler(state)
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    server.state = state  # type: ignore[attr-defined]
    return server


def serve(spec_path: str, host: str, port: int, options: MockOptions) -> int:
    """Serve forever, printing the bound URL on startup."""
    try:
        server = create_server(spec_path, host, port, options)
    except SpecError as exc:
        print(f"apikit: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"apikit: cannot bind {host}:{port}: {exc}", file=sys.stderr)
        return 2
    bound_host, bound_port = server.server_address[:2]
    print(f"apikit mock serving {spec_path}")
    print(f"  http://{bound_host}:{bound_port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        server.server_close()
    return 0
