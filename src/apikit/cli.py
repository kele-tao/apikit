"""Command line interface for apikit.

Exit codes are part of the contract:
  0  success, no findings at or above the failure threshold
  1  findings at or above the failure threshold
  2  usage error, unreadable spec, or an unsupported construct
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from collections.abc import Sequence
from typing import Any

from . import __version__
from .diff import RULES, DiffResult, diff_documents, load_findings
from .generate import GenerationError, generate_example
from .loader import Spec, SpecError
from .mock import MockOptions, serve
from .pointer import ApikitError
from .signer import ReplayCache, SigningError, request_target, sign_request, verify_request

PROG = "apikit"


class Style:
    """ANSI styling that degrades to plain text when colour is off."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, text: str, code: str) -> str:
        return "\x1b[" + code + "m" + text + "\x1b[0m" if self.enabled else text

    def bold(self, text: str) -> str:
        return self._wrap(text, "1")

    def dim(self, text: str) -> str:
        return self._wrap(text, "2")

    def red(self, text: str) -> str:
        return self._wrap(text, "31")

    def green(self, text: str) -> str:
        return self._wrap(text, "32")

    def yellow(self, text: str) -> str:
        return self._wrap(text, "33")

    def blue(self, text: str) -> str:
        return self._wrap(text, "34")

    def cyan(self, text: str) -> str:
        return self._wrap(text, "36")

    def severity(self, severity: str) -> str:
        if severity == "breaking":
            return self.red(self.bold("BREAKING"))
        if severity == "warning":
            return self.yellow("WARNING ")
        return self.blue("INFO    ")


def _color_enabled(stream: Any, no_color: bool) -> bool:
    """Colour when the stream is a TTY, and obey NO_COLOR / FORCE_COLOR."""
    if no_color:
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def _write(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def render_diff_text(result: DiffResult, style: Style) -> str:
    """Render the human terminal report for a diff."""
    lines: list[str] = []
    old_new = (result.old_api_version or "?") + " -> " + (result.new_api_version or "?")
    lines.append(style.bold("apikit diff  (" + old_new + ")"))
    lines.append(
        "  operations: "
        + str(result.old_operations)
        + " -> "
        + str(result.new_operations)
        + "    findings: "
        + str(len(result.findings))
    )
    lines.append("")
    if not result.findings:
        lines.append(style.green("  no changes detected: the two specs are compatible"))
        return "\n".join(lines)
    for severity in ("breaking", "warning", "info"):
        group = [item for item in result.findings if item.severity == severity]
        if not group:
            continue
        lines.append(style.bold(severity.upper() + " (" + str(len(group)) + ")"))
        for item in group:
            marker = style.severity(severity)
            lines.append("  " + marker + " " + style.cyan(item.code) + "  " + item.message)
            suffix = (
                " (+" + str(item.occurrences - 1) + " more locations)"
                if item.occurrences > 1
                else ""
            )
            lines.append("             at " + style.dim(_elide(item.location)) + style.dim(suffix))
            lines.append("             fix: " + item.fix)
        lines.append("")
    counts = (
        str(len(result.breaking))
        + " breaking, "
        + str(len(result.warnings))
        + " warnings, "
        + str(len(result.info))
        + " info"
    )
    colour = style.red if result.breaking else (style.yellow if result.warnings else style.green)
    lines.append(colour("verdict: " + counts))
    lines.append(
        "suggested bump: "
        + style.bold(result.suggested_bump)
        + " ("
        + (result.new_api_version or "?")
        + " -> "
        + result.suggested_version
        + ")"
    )
    return "\n".join(lines)


def render_diff_markdown(result: DiffResult) -> str:
    """Render the GitHub-flavoured Markdown report for a diff."""
    lines = ["## apikit compatibility report", ""]
    lines.append(
        "`" + (result.old_api_version or "?") + "`" + " -> "
        + "`" + (result.new_api_version or "?") + "`" + " | operations "
        + str(result.old_operations)
        + " -> "
        + str(result.new_operations)
        + " | **suggested bump: "
        + result.suggested_bump
        + "**"
    )
    lines.append("")
    if not result.findings:
        lines.append("No compatibility findings: the change is additive-safe.")
        return "\n".join(lines) + "\n"
    lines.append("| severity | rule | location | finding | fix |")
    lines.append("| --- | --- | --- | --- | --- |")
    for item in load_findings(result):
        lines.append(
            "| "
            + item.severity
            + " | "
            + item.code
            + " | `"
            + _md(item.location)
            + "` | "
            + _md(item.message)
            + " | "
            + _md(item.fix)
            + " |"
        )
    counts = (
        str(len(result.breaking))
        + " breaking, "
        + str(len(result.warnings))
        + " warnings, "
        + str(len(result.info))
        + " info"
    )
    lines.append("")
    lines.append(
        "**Verdict:** " + counts + ". Suggested version: `" + result.suggested_version + "`."
    )
    return "\n".join(lines) + "\n"


def _md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _elide(text: str, limit: int = 92) -> str:
    """Shorten a long JSON pointer in the middle so terminal lines stay wrapped free."""
    if len(text) <= limit:
        return text
    head = (limit - 3) * 2 // 3
    tail = limit - 3 - head
    return text[:head] + "..." + text[-tail:]


def cmd_diff(args: argparse.Namespace) -> int:
    style = Style(_color_enabled(sys.stdout, args.no_color))
    try:
        result = diff_documents(Spec.from_file(args.old), Spec.from_file(args.new))
    except SpecError as exc:
        print(PROG + ": " + str(exc), file=sys.stderr)
        return 2
    if args.format == "json":
        payload = result.to_dict()
        payload["fail_on"] = args.fail_on
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    elif args.format == "md":
        sys.stdout.write(render_diff_markdown(result))
    else:
        _write(render_diff_text(result, style))
    return _exit_for(result, args.fail_on)


def _exit_for(result: DiffResult, fail_on: str) -> int:
    if fail_on == "none":
        return 0
    order = {"info": 1, "warning": 2, "breaking": 3}
    threshold = order[fail_on]
    return 1 if any(order[item.severity] >= threshold for item in result.findings) else 0


def cmd_mock(args: argparse.Namespace) -> int:
    if not 0.0 <= args.error_rate <= 1.0:
        print(PROG + ": --error-rate must be between 0 and 1", file=sys.stderr)
        return 2
    options = MockOptions(
        seed=args.seed,
        latency_ms=args.latency,
        error_rate=args.error_rate,
        cors=args.cors,
        stateful=args.stateful,
        color=_color_enabled(sys.stderr, args.no_color),
    )
    return serve(args.spec, args.host, args.port, options)


def _read_secret(args: argparse.Namespace) -> str:
    if args.secret_file:
        try:
            return pathlib.Path(args.secret_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            message = "cannot read secret file " + str(args.secret_file) + ": " + str(exc)
            raise SigningError(message) from exc
    if args.secret is not None:
        return args.secret
    value = os.environ.get("APIKIT_SECRET")
    if not value:
        raise SigningError("no secret: pass --secret, --secret-file or set APIKIT_SECRET")
    return value


def _read_body(args: argparse.Namespace) -> bytes:
    if args.body_file:
        try:
            return pathlib.Path(args.body_file).read_bytes()
        except OSError as exc:
            message = "cannot read body file " + str(args.body_file) + ": " + str(exc)
            raise SigningError(message) from exc
    if args.body_stdin:
        return sys.stdin.buffer.read()
    return (args.body or "").encode("utf-8")


def cmd_sign(args: argparse.Namespace) -> int:
    try:
        secret = _read_secret(args)
        body = _read_body(args)
        if args.url:
            path, query = request_target(args.url)
        else:
            path, query = args.path, args.query or ""
        headers = sign_request(
            secret,
            method=args.method,
            path=path,
            query=query,
            body=body,
            timestamp=args.timestamp,
            nonce=args.nonce,
        )
    except SigningError as exc:
        print(PROG + ": " + str(exc), file=sys.stderr)
        return 2
    if args.format == "json":
        sys.stdout.write(json.dumps(headers, indent=2) + "\n")
    else:
        for key, value in headers.items():
            _write(key + ": " + value)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        secret = _read_secret(args)
        body = _read_body(args)
        if args.url:
            path, query = request_target(args.url)
        else:
            path, query = args.path, args.query or ""
        result = verify_request(
            secret,
            method=args.method,
            path=path,
            query=query,
            body=body,
            timestamp=args.timestamp,
            nonce=args.nonce,
            signature=args.signature,
            window=args.window,
            replay_cache=ReplayCache(args.window) if args.replay_cache else None,
        )
    except SigningError as exc:
        print(PROG + ": " + str(exc), file=sys.stderr)
        return 2
    if args.format == "json":
        payload = {"ok": result.ok, "code": result.code, "reason": result.reason}
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    elif result.ok:
        _write("signature valid")
    else:
        _write("signature invalid [" + result.code + "]: " + result.reason)
    return 0 if result.ok else 1


def build_examples(spec_path: str, base_url: str) -> list[dict[str, str]]:
    """Return one snippet set per operation in the spec."""
    spec = Spec.from_file(spec_path)
    snippets: list[dict[str, str]] = []
    for path, method, operation in spec.operations():
        operation_id = str(operation.get("operationId") or method + "_" + path.strip("/"))
        sample_path = path
        for parameter in operation.get("parameters") or []:
            if isinstance(parameter, dict) and parameter.get("in") == "path":
                name = str(parameter.get("name"))
                schema = parameter.get("schema") or {}
                value = schema.get("example")
                if value is None:
                    value = schema.get("default")
                if value is None:
                    value = 1 if schema.get("type") in ("integer", "number") else "example"
                sample_path = sample_path.replace("{" + name + "}", str(value))
        url = base_url.rstrip("/") + sample_path
        body = _request_body_example(operation)
        body_json = json.dumps(body, ensure_ascii=False, separators=(",", ":")) if body else ""
        if body_json:
            curl = (
                "curl -sS -X "
                + method.upper()
                + " '"
                + url
                + "' -H 'Content-Type: application/json' -d '"
                + body_json
                + "'"
            )
            httpie = (
                "http "
                + method.upper()
                + " "
                + url
                + " Content-Type:application/json <<< '"
                + body_json
                + "'"
            )
            python = (
                "import json, urllib.request\n"
                "payload = json.dumps("
                + body_json
                + ").encode()\n"
                "request = urllib.request.Request(\n"
                "    '" + url + "', data=payload, method='" + method.upper() + "',\n"
                "    headers={'Content-Type': 'application/json'})\n"
                "with urllib.request.urlopen(request) as response:\n"
                "    print(response.status, response.read().decode())"
            )
        else:
            curl = "curl -sS -X " + method.upper() + " '" + url + "'"
            httpie = "http " + method.upper() + " " + url
            python = (
                "import urllib.request\n"
                "request = urllib.request.Request('" + url + "', method='"
                + method.upper() + "')\n"
                "with urllib.request.urlopen(request) as response:\n"
                "    print(response.status, response.read().decode())"
            )
        snippets.append(
            {
                "operationId": operation_id,
                "method": method.upper(),
                "path": sample_path,
                "summary": str(operation.get("summary") or "").strip(),
                "curl": curl,
                "httpie": httpie,
                "python": python,
            }
        )
    return snippets


def _request_body_example(operation: dict[str, Any]) -> Any:
    """Generate a deterministic example body for an operation, if it has one."""
    body = operation.get("requestBody")
    if not isinstance(body, dict):
        return None
    content = body.get("content")
    if not isinstance(content, dict) or "application/json" not in content:
        return None
    media = content["application/json"]
    if not isinstance(media, dict):
        return None
    example = media.get("example")
    if example is not None:
        return example
    schema = media.get("schema")
    if not isinstance(schema, dict):
        return None
    try:
        return generate_example(schema, seed=0, name=str(operation.get("operationId") or "body"))
    except GenerationError:
        return None


def cmd_examples(args: argparse.Namespace) -> int:
    try:
        snippets = build_examples(args.spec, args.base_url)
    except SpecError as exc:
        print(PROG + ": " + str(exc), file=sys.stderr)
        return 2
    if args.format == "json":
        sys.stdout.write(json.dumps(snippets, indent=2, ensure_ascii=False) + "\n")
        return 0
    if not snippets:
        _write("no operations found in the spec")
        return 0
    for index, snippet in enumerate(snippets):
        if index:
            _write()
        title = snippet["method"] + " " + snippet["path"]
        if snippet["summary"]:
            title += "  (" + snippet["summary"] + ")"
        _write(title)
        _write("-" * len(title))
        _write("curl:")
        _write("  " + snippet["curl"])
        _write("httpie:")
        _write("  " + snippet["httpie"])
        _write("python:")
        for line in snippet["python"].splitlines():
            _write("  " + line)
    return 0


def cmd_rules(args: argparse.Namespace) -> int:
    if args.format == "json":
        payload = [
            {"code": code, "severity": severity, "description": text}
            for code, (severity, text) in sorted(RULES.items())
        ]
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return 0
    style = Style(_color_enabled(sys.stdout, args.no_color))
    _write(style.bold(str(len(RULES)) + " documented rules"))
    for code, (severity, text) in sorted(RULES.items()):
        _write("  " + style.cyan(code) + "  " + style.severity(severity) + "  " + text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--no-color", action="store_true", default=argparse.SUPPRESS, help="disable ANSI colours"
    )
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Catch breaking API changes, mock the contract, sign requests.",
        epilog="Exit codes: 0 ok, 1 findings at or above the fail threshold, 2 usage error.",
    )
    parser.add_argument("--version", action="version", version=PROG + " " + __version__)
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colours")
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    diff_parser = subparsers.add_parser(
        "diff",
        parents=[common],
        help="compare two OpenAPI 3 specs and report compatibility findings",
    )
    diff_parser.add_argument("old", help="baseline spec (JSON or YAML)")
    diff_parser.add_argument("new", help="candidate spec (JSON or YAML)")
    diff_parser.add_argument(
        "--fail-on",
        choices=("breaking", "warning", "info", "none"),
        default="breaking",
        help="lowest severity that makes the command exit 1 (default: breaking)",
    )
    diff_parser.add_argument(
        "--format", choices=("text", "md", "json"), default="text", help="output format"
    )
    diff_parser.set_defaults(handler=cmd_diff)

    mock_parser = subparsers.add_parser(
        "mock", parents=[common], help="serve a deterministic mock API from a spec"
    )
    mock_parser.add_argument("spec", help="OpenAPI 3 spec to serve")
    mock_parser.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    mock_parser.add_argument(
        "--port", type=int, default=8787, help="port (default 8787, 0 = ephemeral)"
    )
    mock_parser.add_argument("--seed", type=int, default=0, help="deterministic generation seed")
    mock_parser.add_argument("--latency", type=int, default=0, help="artificial latency in ms")
    mock_parser.add_argument(
        "--error-rate", type=float, default=0.0, help="fraction of requests answered with 503"
    )
    mock_parser.add_argument("--cors", action="store_true", help="send permissive CORS headers")
    mock_parser.add_argument(
        "--stateful", action="store_true", help="POST creates records that GET can return"
    )
    mock_parser.set_defaults(handler=cmd_mock)

    sign_parser = subparsers.add_parser(
        "sign", parents=[common], help="sign a request with HMAC-SHA256"
    )
    _add_request_arguments(sign_parser)
    sign_parser.add_argument("--secret", help="signing secret (prefer --secret-file)")
    sign_parser.add_argument("--secret-file", help="file containing the signing secret")
    sign_parser.add_argument("--timestamp", type=int, help="override the unix timestamp")
    sign_parser.add_argument("--nonce", help="override the nonce")
    sign_parser.add_argument("--format", choices=("text", "json"), default="text")
    sign_parser.set_defaults(handler=cmd_sign)

    verify_parser = subparsers.add_parser(
        "verify", parents=[common], help="verify a signed request"
    )
    _add_request_arguments(verify_parser)
    verify_parser.add_argument("--secret", help="signing secret (prefer --secret-file)")
    verify_parser.add_argument("--secret-file", help="file containing the signing secret")
    verify_parser.add_argument("--timestamp", help="X-Apikit-Timestamp value")
    verify_parser.add_argument("--nonce", help="X-Apikit-Nonce value")
    verify_parser.add_argument("--signature", help="X-Apikit-Signature value")
    verify_parser.add_argument("--window", type=int, default=300, help="replay window in seconds")
    verify_parser.add_argument(
        "--replay-cache", action="store_true", help="reject a nonce that was already accepted"
    )
    verify_parser.add_argument("--format", choices=("text", "json"), default="text")
    verify_parser.set_defaults(handler=cmd_verify)

    examples_parser = subparsers.add_parser(
        "examples",
        parents=[common],
        help="print curl, HTTPie and Python snippets for every operation",
    )
    examples_parser.add_argument("spec", help="OpenAPI 3 spec")
    examples_parser.add_argument(
        "--base-url", default="http://127.0.0.1:8787", help="base URL used in the snippets"
    )
    examples_parser.add_argument("--format", choices=("text", "json"), default="text")
    examples_parser.set_defaults(handler=cmd_examples)

    rules_parser = subparsers.add_parser(
        "rules", parents=[common], help="list every diff rule and its severity"
    )
    rules_parser.add_argument("--format", choices=("text", "json"), default="text")
    rules_parser.set_defaults(handler=cmd_rules)
    return parser


def _add_request_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--method", default="GET", help="HTTP method (default GET)")
    parser.add_argument("--url", help="full URL; path and query are extracted from it")
    parser.add_argument("--path", default="/", help="request path when --url is not used")
    parser.add_argument("--query", help="raw query string when --url is not used")
    parser.add_argument("--body", help="request body as a string")
    parser.add_argument("--body-file", help="request body read from a file")
    parser.add_argument("--body-stdin", action="store_true", help="request body read from stdin")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    try:
        return int(args.handler(args))
    except ApikitError as exc:
        print(PROG + ": " + str(exc), file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
