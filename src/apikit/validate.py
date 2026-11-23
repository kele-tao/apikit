"""A pragmatic JSON Schema validator for request checking.

This is not a complete JSON Schema implementation: it covers the keywords an
HTTP mock server needs to answer "would the real API accept this request?".
Unsupported keywords are ignored on purpose, and that behaviour is documented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_URI = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^\s]+$")


@dataclass(frozen=True)
class Issue:
    """One validation failure, anchored at a human-readable path."""

    path: str
    message: str

    def render(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def validate_value(
    value: Any, schema: Any, path: str = "$", _depth: int = 0, issues: list[Issue] | None = None
) -> list[Issue]:
    """Validate *value* against *schema*, returning every issue found."""
    if issues is None:
        issues = []
    if _depth > 24 or not isinstance(schema, dict):
        return issues

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        if not any(_type_ok(value, str(item)) for item in schema_type):
            issues.append(Issue(path, f"expected one of {schema_type}, got {_type_name(value)}"))
            return issues
    elif isinstance(schema_type, str) and not _type_ok(value, schema_type):
        issues.append(Issue(path, f"expected {schema_type}, got {_type_name(value)}"))
        return issues

    if "const" in schema and value != schema["const"]:
        issues.append(Issue(path, f"expected constant {schema['const']!r}, got {value!r}"))
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        issues.append(Issue(path, f"expected one of {enum}, got {value!r}"))

    if isinstance(value, str):
        _validate_string(value, schema, path, issues)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        _validate_number(value, schema, path, issues)
    if isinstance(value, dict):
        _validate_object(value, schema, path, _depth, issues)
    if isinstance(value, list):
        _validate_array(value, schema, path, _depth, issues)

    _validate_composition(value, schema, path, _depth, issues)
    return issues


def _validate_string(value: str, schema: dict[str, Any], path: str, issues: list[Issue]) -> None:
    minimum = schema.get("minLength")
    if isinstance(minimum, int) and len(value) < minimum:
        issues.append(Issue(path, f"string shorter than minLength {minimum}"))
    maximum = schema.get("maxLength")
    if isinstance(maximum, int) and len(value) > maximum:
        issues.append(Issue(path, f"string longer than maxLength {maximum}"))
    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        try:
            if not re.search(pattern, value):
                issues.append(Issue(path, f"does not match pattern {pattern!r}"))
        except re.error:
            issues.append(Issue(path, f"schema pattern {pattern!r} is not a valid regex"))
    fmt = schema.get("format")
    if fmt == "email" and not _EMAIL.match(value):
        issues.append(Issue(path, "not a valid email address"))
    elif fmt == "date" and not _DATE.match(value):
        issues.append(Issue(path, "not a valid date (expected YYYY-MM-DD)"))
    elif fmt in ("date-time", "datetime") and not _DATETIME.match(value):
        issues.append(Issue(path, "not a valid date-time"))
    elif fmt == "uuid" and not _UUID.match(value):
        issues.append(Issue(path, "not a valid uuid"))
    elif fmt in ("uri", "url") and not _URI.match(value):
        issues.append(Issue(path, "not a valid absolute uri"))


def _validate_number(
    value: int | float, schema: dict[str, Any], path: str, issues: list[Issue]
) -> None:
    minimum = schema.get("minimum")
    if isinstance(minimum, (int, float)) and value < minimum:
        issues.append(Issue(path, f"{value} is below minimum {minimum}"))
    maximum = schema.get("maximum")
    if isinstance(maximum, (int, float)) and value > maximum:
        issues.append(Issue(path, f"{value} is above maximum {maximum}"))
    exclusive_min = schema.get("exclusiveMinimum")
    if isinstance(exclusive_min, (int, float)) and value <= exclusive_min:
        issues.append(Issue(path, f"{value} must be greater than {exclusive_min}"))
    exclusive_max = schema.get("exclusiveMaximum")
    if isinstance(exclusive_max, (int, float)) and value >= exclusive_max:
        issues.append(Issue(path, f"{value} must be less than {exclusive_max}"))
    multiple = schema.get("multipleOf")
    if isinstance(multiple, (int, float)) and multiple:
        quotient = value / multiple
        if abs(quotient - round(quotient)) > 1e-9:
            issues.append(Issue(path, f"{value} is not a multiple of {multiple}"))


def _validate_object(
    value: dict[str, Any],
    schema: dict[str, Any],
    path: str,
    depth: int,
    issues: list[Issue],
) -> None:
    required = schema.get("required")
    if isinstance(required, list):
        for name in required:
            if name not in value:
                issues.append(Issue(path, f"missing required property {name!r}"))
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, child in properties.items():
            if name in value:
                validate_value(value[name], child, f"{path}.{name}", depth + 1, issues)
    additional = schema.get("additionalProperties")
    known = set(properties) if isinstance(properties, dict) else set()
    if additional is False:
        for name in value:
            if name not in known:
                issues.append(Issue(path, f"unexpected property {name!r}"))
    elif isinstance(additional, dict):
        for name, child_value in value.items():
            if name not in known:
                validate_value(child_value, additional, f"{path}.{name}", depth + 1, issues)


def _validate_array(
    value: list[Any],
    schema: dict[str, Any],
    path: str,
    depth: int,
    issues: list[Issue],
) -> None:
    minimum = schema.get("minItems")
    if isinstance(minimum, int) and len(value) < minimum:
        issues.append(Issue(path, f"array needs at least {minimum} items"))
    maximum = schema.get("maxItems")
    if isinstance(maximum, int) and len(value) > maximum:
        issues.append(Issue(path, f"array allows at most {maximum} items"))
    items = schema.get("items")
    if isinstance(items, dict):
        for index, item in enumerate(value):
            validate_value(item, items, f"{path}[{index}]", depth + 1, issues)


def _validate_composition(
    value: Any, schema: dict[str, Any], path: str, depth: int, issues: list[Issue]
) -> None:
    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for part in all_of:
            validate_value(value, part, path, depth + 1, issues)
    one_of = schema.get("oneOf")
    if isinstance(one_of, list) and one_of:
        matches = sum(1 for part in one_of if not validate_value(value, part, path, depth + 1, []))
        if matches != 1:
            issues.append(Issue(path, f"value matches {matches} of the oneOf variants, expected 1"))
    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and any_of:
        if not any(not validate_value(value, part, path, depth + 1, []) for part in any_of):
            issues.append(Issue(path, "value matches none of the anyOf variants"))
    not_schema = schema.get("not")
    if isinstance(not_schema, dict):
        if not validate_value(value, not_schema, path, depth + 1, []):
            issues.append(Issue(path, "value matches a schema it must not match"))


def validate_request_body(body: Any, schema: Any) -> list[Issue]:
    """Validate a decoded JSON body, returning style issues for RFC 7807 output."""
    return validate_value(body, schema, "body")


def coerce_param(raw: str, schema: dict[str, Any]) -> tuple[Any, Issue | None]:
    """Coerce a path or query string into the type the schema declares."""
    schema_type = schema.get("type")
    if schema_type == "integer":
        try:
            return int(raw), None
        except ValueError:
            return raw, Issue("$", f"expected integer, got {raw!r}")
    if schema_type == "number":
        try:
            return float(raw), None
        except ValueError:
            return raw, Issue("$", f"expected number, got {raw!r}")
    if schema_type == "boolean":
        lowered = raw.lower()
        if lowered in ("true", "1"):
            return True, None
        if lowered in ("false", "0"):
            return False, None
        return raw, Issue("$", f"expected boolean, got {raw!r}")
    if schema_type == "array":
        return [item for item in raw.split(",") if item != ""], None
    return raw, None
