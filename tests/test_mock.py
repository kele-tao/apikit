"""Tests for the deterministic mock server."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest
from conftest import V1

from apikit.generate import generate_example
from apikit.mock import MockOptions, create_server, problem
from apikit.validate import Issue, validate_value


@pytest.fixture()
def server():
    options = MockOptions(seed=42, quiet=True)
    httpd = create_server(str(V1), port=0, options=options)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def get(url: str) -> tuple[int, bytes, dict[str, str]]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def post(url: str, payload: dict) -> tuple[int, bytes, dict[str, str]]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def test_list_pets_returns_generated_body(server):
    status, body, headers = get(server + "/pets")
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    payload = json.loads(body)
    assert isinstance(payload, list) and payload
    assert {"id", "name"} <= set(payload[0])


def test_generation_is_deterministic_across_restarts():
    options = MockOptions(seed=7, quiet=True)
    first = create_server(str(V1), port=0, options=options)
    second = create_server(str(V1), port=0, options=options)
    for httpd in (first, second):
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        httpd.daemon_threads = True
    try:
        host1, port1 = first.server_address[:2]
        host2, port2 = second.server_address[:2]
        body1 = get(f"http://{host1}:{port1}/pets")[1]
        body2 = get(f"http://{host2}:{port2}/pets")[1]
        assert body1 == body2
    finally:
        for httpd in (first, second):
            httpd.shutdown()
            httpd.server_close()


def test_unknown_route_returns_problem_json(server):
    status, body, headers = get(server + "/nope")
    assert status == 404
    assert "problem+json" in headers["Content-Type"]
    payload = json.loads(body)
    assert payload["status"] == 404
    assert "no route" in payload["detail"]


def test_wrong_method_returns_405_with_allow(server):
    status, body, headers = get(server + "/store/orders")
    assert status == 405
    assert "POST" in headers.get("Allow", "")
    assert json.loads(body)["status"] == 405


def test_query_parameter_validation(server):
    status, body, _ = get(server + "/pets?limit=notanumber")
    assert status == 422
    payload = json.loads(body)
    assert payload["status"] == 422
    assert any("integer" in item["detail"] for item in payload["errors"])


def test_query_parameter_bounds(server):
    status, _, _ = get(server + "/pets?limit=500")
    assert status == 422


def test_valid_query_is_accepted(server):
    status, _, _ = get(server + "/pets?limit=5&tag=dog")
    assert status == 200


def test_body_validation_returns_field_errors(server):
    status, body, _ = post(server + "/pets", {"name": "", "tag": "dragon"})
    assert status == 422
    payload = json.loads(body)
    pointers = {item["pointer"] for item in payload["errors"]}
    assert "body.name" in pointers
    assert "body.tag" in pointers


def test_valid_body_is_accepted(server):
    status, body, _ = post(server + "/pets", {"name": "Rex", "tag": "dog"})
    assert status == 201
    created = json.loads(body)
    assert {"id", "name", "tag"} <= set(created)


def test_malformed_json_returns_400(server):
    request = urllib.request.Request(
        server + "/pets",
        data=b"{broken",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        assert "bad request" in exc.read().decode().lower()
    assert status == 400


def test_cors_headers_when_enabled():
    options = MockOptions(quiet=True, cors=True)
    httpd = create_server(str(V1), port=0, options=options)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]
        _, _, headers = get(f"http://{host}:{port}/pets")
        assert headers["Access-Control-Allow-Origin"] == "*"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_error_rate_is_reproducible_for_a_seed():
    options = MockOptions(quiet=True, error_rate=1.0, seed=1)
    httpd = create_server(str(V1), port=0, options=options)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]
        for _ in range(3):
            status, body, _ = get(f"http://{host}:{port}/pets")
            assert status == 503
            assert "injected failure" in json.loads(body)["detail"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_path_parameter_coercion(server):
    status, body, _ = get(server + "/pets/abc")
    assert status == 422
    assert json.loads(body)["errors"][0]["pointer"] == "path.petId"


def test_stateful_post_then_get():
    options = MockOptions(quiet=True, stateful=True)
    httpd = create_server(str(V1), port=0, options=options)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]
        base = f"http://{host}:{port}"
        status, body, _ = post(base + "/pets", {"name": "Nova", "tag": "cat"})
        assert status == 201
        created = json.loads(body)
        assert created["id"] == 1
        status, body, _ = get(base + "/pets/1")
        assert status == 200
        assert json.loads(body)["name"] == "Nova"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_generate_example_formats():
    schema = {
        "type": "object",
        "properties": {
            "email": {"type": "string", "format": "email"},
            "uuid": {"type": "string", "format": "uuid"},
            "when": {"type": "string", "format": "date-time"},
            "kind": {"type": "string", "enum": ["a", "b"]},
            "count": {"type": "integer", "minimum": 5, "maximum": 9},
            "flag": {"type": "boolean"},
            "tags": {"type": "array", "items": {"type": "string"}, "minItems": 3},
        },
    }
    value = generate_example(schema, seed=3)
    assert value["email"].endswith("@example.com")
    assert len(value["uuid"]) == 36
    assert value["when"].startswith("2026-")
    assert value["kind"] in {"a", "b"}
    assert 5 <= value["count"] <= 9
    assert isinstance(value["flag"], bool)
    assert len(value["tags"]) == 3


def test_generate_example_is_stable_for_a_seed():
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    assert generate_example(schema, seed=5) == generate_example(schema, seed=5)
    assert generate_example(schema, seed=5) != generate_example(schema, seed=6)


def test_generate_example_honours_pattern():
    schema = {"type": "string", "pattern": "^[A-Z]{3}-[0-9]{4}$"}
    value = generate_example(schema, seed=11)
    import re

    assert re.match(r"^[A-Z]{3}-[0-9]{4}$", value)


def test_generate_example_handles_oneof_and_allof():
    one = {"oneOf": [{"type": "string", "enum": ["x"]}, {"type": "integer"}]}
    assert generate_example(one) == "x"
    all_schema = {"allOf": [{"type": "object", "properties": {"a": {"type": "integer"}}},
                            {"type": "object", "properties": {"b": {"type": "string"}}}]}
    value = generate_example(all_schema)
    assert set(value) == {"a", "b"}


def test_validate_value_reports_nested_paths():
    schema = {
        "type": "object",
        "required": ["items"],
        "properties": {"items": {"type": "array", "items": {"type": "integer"}}},
    }
    issues = validate_value({"items": [1, "two"]}, schema)
    assert [issue.path for issue in issues] == ["$.items[1]"]


def test_validate_value_enum_and_format():
    schema = {"type": "string", "format": "email"}
    assert [issue.message for issue in validate_value("nope", schema)] == [
        "not a valid email address"
    ]


def test_problem_document_is_rfc7807_shaped():
    payload = json.loads(problem(404, "Not Found", "nope", [Issue("body.x", "bad")]))
    assert payload["type"].startswith("https://apikit.dev/problems/")
    assert payload["status"] == 404
    assert payload["errors"] == [{"pointer": "body.x", "detail": "bad"}]
