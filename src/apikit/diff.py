"""Severity-aware OpenAPI 3 diff engine.

The engine compares two parsed documents (already dereferenced) and emits
:class:Finding records. Every rule has a stable code, a fixed severity and a
documented meaning; docs/RULES.md is generated from this registry, so the
documentation cannot drift away from the implementation.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .loader import Spec
from .pointer import escape, join

SEVERITIES = ("breaking", "warning", "info")
_RANK = {"breaking": 3, "warning": 2, "info": 1}
_METHOD_ORDER = ("get", "put", "post", "delete", "options", "head", "patch", "trace")

#: code -> (severity, one-line description)
RULES: dict[str, tuple[str, str]] = {
    "AP001": ("breaking", "Path removed"),
    "AP002": ("info", "Path added"),
    "AP003": ("breaking", "Operation removed"),
    "AP004": ("info", "Operation added"),
    "AP005": ("breaking", "Response status code removed"),
    "AP006": ("info", "Response status code added"),
    "AP007": ("breaking", "Request body became required"),
    "AP008": ("info", "Request body became optional"),
    "AP009": ("breaking", "Request body removed"),
    "AP010": ("breaking", "New required property in a request"),
    "AP011": ("breaking", "Property removed from a response"),
    "AP012": ("warning", "Property removed from a request"),
    "AP013": ("breaking", "Property renamed"),
    "AP014": ("breaking", "Property type changed"),
    "AP015": ("info", "Numeric type widened (integer to number)"),
    "AP016": ("breaking", "Enum value removed"),
    "AP017": ("warning", "Enum value added"),
    "AP018": ("breaking", "Optional property became required"),
    "AP019": ("info", "Required property became optional"),
    "AP020": ("breaking", "New required parameter"),
    "AP021": ("warning", "Parameter removed"),
    "AP022": ("breaking", "Parameter became required"),
    "AP023": ("info", "Parameter became optional"),
    "AP024": ("breaking", "Security scheme removed"),
    "AP025": ("breaking", "Security requirement tightened"),
    "AP026": ("breaking", "Constraint narrowed"),
    "AP027": ("info", "Constraint loosened"),
    "AP028": ("warning", "Default value changed"),
    "AP029": ("warning", "Format changed"),
    "AP030": ("breaking", "additionalProperties restricted"),
    "AP031": ("breaking", "Schema variant removed (oneOf/anyOf)"),
    "AP032": ("warning", "Discriminator mapping changed"),
    "AP033": ("warning", "Operation id renamed"),
    "AP034": ("warning", "Operation deprecated"),
    "AP035": ("info", "Security requirement removed"),
    "AP036": ("breaking", "Parameter type changed"),
    "AP037": ("info", "Property added"),
    "AP038": ("info", "Response property became required"),
    "AP039": ("breaking", "Response body removed"),
    "AP040": ("breaking", "Media type removed"),
    "AP041": ("info", "Schema variant added (oneOf/anyOf)"),
}

_NARROWING = (
    # (keyword, direction that narrows, human label)
    ("minimum", "up", "minimum raised"),
    ("maximum", "down", "maximum lowered"),
    ("exclusiveMinimum", "up", "exclusiveMinimum raised"),
    ("exclusiveMaximum", "down", "exclusiveMaximum lowered"),
    ("minLength", "up", "minLength raised"),
    ("maxLength", "down", "maxLength lowered"),
    ("minItems", "up", "minItems raised"),
    ("maxItems", "down", "maxItems lowered"),
    ("minProperties", "up", "minProperties raised"),
    ("maxProperties", "down", "maxProperties lowered"),
)


@dataclass(frozen=True)
class Finding:
    """One detected difference between two specs.

    A shared schema is dereferenced into every operation that uses it, so the
    same incompatibility appears several times. Findings that share a code and
    message are merged: location is the first JSON pointer, locations holds
    every affected pointer and occurrences is their count.
    """

    code: str
    severity: str
    location: str
    message: str
    fix: str
    operation: str = ""
    locations: tuple[str, ...] = ()

    @property
    def occurrences(self) -> int:
        return max(1, len(self.locations))

    def merged(self, others: list[Finding]) -> Finding:
        """Return a copy of this finding with every location from *others*."""
        seen: list[str] = list(self.locations) or [self.location]
        for other in others:
            for location in other.locations or (other.location,):
                if location not in seen:
                    seen.append(location)
        return Finding(
            code=self.code,
            severity=self.severity,
            location=seen[0],
            message=self.message,
            fix=self.fix,
            operation=self.operation,
            locations=tuple(seen),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "location": self.location,
            "locations": list(self.locations) or [self.location],
            "occurrences": self.occurrences,
            "message": self.message,
            "fix": self.fix,
            "operation": self.operation,
        }


@dataclass
class DiffResult:
    """Aggregate result of comparing two specs."""

    old_version: str
    new_version: str
    old_api_version: str
    new_api_version: str
    findings: list[Finding] = field(default_factory=list)
    old_operations: int = 0
    new_operations: int = 0

    def of_severity(self, *severities: str) -> list[Finding]:
        wanted = set(severities)
        return [item for item in self.findings if item.severity in wanted]

    @property
    def breaking(self) -> list[Finding]:
        return self.of_severity("breaking")

    @property
    def warnings(self) -> list[Finding]:
        return self.of_severity("warning")

    @property
    def info(self) -> list[Finding]:
        return self.of_severity("info")

    @property
    def suggested_bump(self) -> str:
        if self.breaking:
            return "major"
        if self.warnings:
            return "minor"
        if self.info:
            return "patch"
        return "none"

    @property
    def suggested_version(self) -> str:
        return bump_version(self.new_api_version or self.old_api_version, self.suggested_bump)

    def to_dict(self) -> dict[str, Any]:
        return {
            "old": {"openapi": self.old_version, "version": self.old_api_version,
                    "operations": self.old_operations},
            "new": {"openapi": self.new_version, "version": self.new_api_version,
                    "operations": self.new_operations},
            "suggested_bump": self.suggested_bump,
            "suggested_version": self.suggested_version,
            "counts": {
                "breaking": len(self.breaking),
                "warning": len(self.warnings),
                "info": len(self.info),
                "total": len(self.findings),
            },
            "findings": [item.as_dict() for item in self.findings],
        }


def bump_version(version: str, bump: str) -> str:
    """Apply a semver bump to *version*, returning the new version string."""
    if bump == "none":
        return version or "0.0.0"
    parts = (version or "0.0.0").split(".")
    try:
        numbers = [int(part) for part in parts[:3]]
    except ValueError:
        return version or "0.0.0"
    while len(numbers) < 3:
        numbers.append(0)
    major, minor, patch = numbers
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    return version


def _api_version(document: dict[str, Any]) -> str:
    info = document.get("info")
    if isinstance(info, dict):
        value = info.get("version")
        if isinstance(value, str):
            return value
    return ""


def diff_documents(old: dict[str, Any], new: dict[str, Any]) -> DiffResult:
    """Compare two OpenAPI documents and return a :class:DiffResult."""
    old_spec = Spec(old) if not isinstance(old, Spec) else old
    new_spec = Spec(new) if not isinstance(new, Spec) else new
    comparer = _Comparer(old_spec, new_spec)
    comparer.run()
    return DiffResult(
        old_version=old_spec.version,
        new_version=new_spec.version,
        old_api_version=_api_version(old_spec.document),
        new_api_version=_api_version(new_spec.document),
        findings=comparer.findings,
        old_operations=sum(1 for _ in old_spec.operations()),
        new_operations=sum(1 for _ in new_spec.operations()),
    )


def diff_specs(old: dict[str, Any], new: dict[str, Any]) -> DiffResult:
    """Public alias used by library consumers: compare two parsed specs."""
    return diff_documents(old, new)


def diff_files(old_path: str, new_path: str) -> DiffResult:
    """Load two spec files from disk and diff them."""
    return diff_documents(Spec.from_file(old_path), Spec.from_file(new_path))


class _Comparer:
    """Walks two dereferenced specs and appends findings in document order."""

    def __init__(self, old: Spec, new: Spec) -> None:
        self.old = old
        self.new = new
        self.findings: list[Finding] = []

    # -- helpers ---------------------------------------------------------
    def add(self, code: str, location: str, message: str, fix: str, operation: str = "") -> None:
        severity = RULES[code][0]
        self.findings.append(Finding(code, severity, location, message, fix, operation))

    # -- entry point -----------------------------------------------------
    def run(self) -> None:
        self._compare_paths()
        self._compare_security_schemes()
        self._merge_duplicates()

    def _merge_duplicates(self) -> None:
        grouped: dict[tuple[str, str, str], list[Finding]] = {}
        for finding in self.findings:
            grouped.setdefault((finding.code, finding.message, finding.fix), []).append(finding)
        merged: list[Finding] = []
        for key, group in grouped.items():
            del key
            base = group[0]
            if len(group) > 1:
                base = base.merged(group[1:])
            merged.append(base)
        self.findings = merged

    def _compare_paths(self) -> None:
        old_paths = self.old.deref(self.old.paths())
        new_paths = self.new.deref(self.new.paths())
        for path in sorted(old_paths):
            location = "paths" + join(path)
            if path not in new_paths:
                self.add(
                    "AP001",
                    location,
                    f"path {path} was removed",
                    f"restore {path}, or ship the removal in a new major version",
                )
                continue
            self._compare_path_item(path, old_paths[path], new_paths[path])
        for path in sorted(set(new_paths) - set(old_paths)):
            self.add(
                "AP002",
                "paths" + join(path),
                f"path {path} was added",
                "additive change: clients can adopt it whenever convenient",
            )

    # -- path items ------------------------------------------------------
    def _compare_path_item(
        self, path: str, old_item: dict[str, Any], new_item: dict[str, Any]
    ) -> None:
        for method in _METHOD_ORDER:
            old_op = old_item.get(method)
            new_op = new_item.get(method)
            if isinstance(old_op, dict) and not isinstance(new_op, dict):
                self.add(
                    "AP003",
                    "paths" + join(path, method),
                    f"{method.upper()} {path} was removed",
                    "keep the operation, or remove it only in a major release",
                    f"{method.upper()} {path}",
                )
            elif not isinstance(old_op, dict) and isinstance(new_op, dict):
                self.add(
                    "AP004",
                    "paths" + join(path, method),
                    f"{method.upper()} {path} was added",
                    "additive change: document it in the changelog",
                    f"{method.upper()} {path}",
                )
            elif isinstance(old_op, dict) and isinstance(new_op, dict):
                self._compare_operation(path, method, old_op, new_op)

    def _compare_operation(
        self, path: str, method: str, old_op: dict[str, Any], new_op: dict[str, Any]
    ) -> None:
        base = "paths" + join(path, method)
        operation = f"{method.upper()} {path}"
        old_id = old_op.get("operationId")
        new_id = new_op.get("operationId")
        if old_id != new_id and (old_id or new_id):
            self.add(
                "AP033",
                base + "/operationId",
                f"operationId changed from {old_id!r} to {new_id!r}",
                "keep the old operationId as an alias, or treat client generators as broken",
                operation,
            )
        if not old_op.get("deprecated") and new_op.get("deprecated"):
            self.add(
                "AP034",
                base + "/deprecated",
                "operation is now deprecated",
                "announce a removal date and point users at the replacement",
                operation,
            )
        self._compare_parameters(path, method, old_op, new_op)
        self._compare_request_body(base, operation, old_op, new_op)
        self._compare_responses(base, operation, old_op, new_op)
        self._compare_operation_security(base, operation, old_op, new_op)

    # -- parameters ------------------------------------------------------
    def _compare_parameters(
        self, path: str, method: str, old_op: dict[str, Any], new_op: dict[str, Any]
    ) -> None:
        base = "paths" + join(path, method)
        operation = f"{method.upper()} {path}"
        old_params = _parameters_by_key(old_op.get("parameters"))
        new_params = _parameters_by_key(new_op.get("parameters"))
        for index, key in enumerate(sorted(old_params)):
            name, where = key
            location = base + join("parameters", str(index))
            if key not in new_params:
                self.add(
                    "AP021",
                    location,
                    f"parameter {name!r} (in: {where}) was removed",
                    "keep accepting the parameter and ignore it for one deprecation cycle",
                    operation,
                )
                continue
            old_param = old_params[key]
            new_param = new_params[key]
            if not old_param.get("required") and new_param.get("required"):
                self.add(
                    "AP022",
                    location + "/required",
                    f"parameter {name!r} (in: {where}) became required",
                    "give it a default, or bump the major version",
                    operation,
                )
            elif old_param.get("required") and not new_param.get("required"):
                self.add(
                    "AP023",
                    location + "/required",
                    f"parameter {name!r} (in: {where}) became optional",
                    "no client change needed; update the docs",
                    operation,
                )
            self._compare_parameter_schema(location, operation, name, old_param, new_param)
        for offset, key in enumerate(sorted(set(new_params) - set(old_params))):
            name, where = key
            new_param = new_params[key]
            location = base + join("parameters", str(len(old_params) + offset))
            if new_param.get("required"):
                self.add(
                    "AP020",
                    location,
                    f"new required parameter {name!r} (in: {where})",
                    "make it optional or defaulted, otherwise every existing call fails",
                    operation,
                )
            else:
                self.add(
                    "AP041",
                    location,
                    f"new optional parameter {name!r} (in: {where})",
                    "additive change: document it in the changelog",
                    operation,
                )

    def _compare_parameter_schema(
        self,
        location: str,
        operation: str,
        name: str,
        old_param: dict[str, Any],
        new_param: dict[str, Any],
    ) -> None:
        old_schema = old_param.get("schema") or _schema_from_content(old_param.get("content"))
        new_schema = new_param.get("schema") or _schema_from_content(new_param.get("content"))
        if not isinstance(old_schema, dict) or not isinstance(new_schema, dict):
            return
        old_type = old_schema.get("type")
        new_type = new_schema.get("type")
        if old_type and new_type and old_type != new_type:
            self.add(
                "AP036",
                location + "/schema/type",
                f"parameter {name!r} type changed from {old_type!r} to {new_type!r}",
                f"accept {old_type} for one release, or bump the major version",
                operation,
            )
            return
        self._compare_schema(old_schema, new_schema, location + "/schema", "request", operation)

    # -- request body ----------------------------------------------------
    def _compare_request_body(
        self, base: str, operation: str, old_op: dict[str, Any], new_op: dict[str, Any]
    ) -> None:
        old_body = old_op.get("requestBody")
        new_body = new_op.get("requestBody")
        if isinstance(old_body, dict) and not isinstance(new_body, dict):
            self.add(
                "AP009",
                base + "/requestBody",
                "request body was removed",
                "keep the body optional for a release; clients send it today",
                operation,
            )
            return
        if not isinstance(old_body, dict) or not isinstance(new_body, dict):
            return
        old_required = bool(old_body.get("required"))
        new_required = bool(new_body.get("required"))
        if old_required != new_required:
            code = "AP007" if new_required else "AP008"
            self.add(
                code,
                base + "/requestBody/required",
                "request body became required" if new_required else "request body became optional",
                "send the body in every call" if new_required else "no client change needed",
                operation,
            )
        self._compare_content(
            base + "/requestBody/content",
            operation,
            old_body.get("content"),
            new_body.get("content"),
            "request",
        )

    # -- responses -------------------------------------------------------
    def _compare_responses(
        self, base: str, operation: str, old_op: dict[str, Any], new_op: dict[str, Any]
    ) -> None:
        old_responses = old_op.get("responses") or {}
        new_responses = new_op.get("responses") or {}
        if not isinstance(old_responses, dict) or not isinstance(new_responses, dict):
            return
        for status in sorted(old_responses, key=str):
            location = base + join("responses", str(status))
            if status not in new_responses:
                self.add(
                    "AP005",
                    location,
                    f"response {status} was removed",
                    f"keep {status} in the contract, or bump the major version",
                    operation,
                )
                continue
            old_response = old_responses[status]
            new_response = new_responses[status]
            if not isinstance(old_response, dict) or not isinstance(new_response, dict):
                continue
            old_content = old_response.get("content")
            new_content = new_response.get("content")
            if old_content and not new_content:
                self.add(
                    "AP039",
                    location + "/content",
                    f"response {status} lost its body",
                    "restore the body or return 204 explicitly and document the change",
                    operation,
                )
                continue
            self._compare_content(
                location + "/content", operation, old_content, new_content, "response"
            )
        for status in sorted(set(new_responses) - set(old_responses), key=str):
            self.add(
                "AP006",
                base + join("responses", str(status)),
                f"response {status} was added",
                "additive change: make sure clients handle it",
                operation,
            )

    # -- media types -----------------------------------------------------
    def _compare_content(
        self,
        base: str,
        operation: str,
        old_content: Any,
        new_content: Any,
        direction: str,
    ) -> None:
        if not isinstance(old_content, dict) or not isinstance(new_content, dict):
            return
        for media in sorted(old_content):
            if media not in new_content:
                self.add(
                    "AP040",
                    base + join(media),
                    f"media type {media} was removed",
                    "keep accepting or returning it until the next major version",
                    operation,
                )
                continue
            old_media = old_content[media]
            new_media = new_content[media]
            if not isinstance(old_media, dict) or not isinstance(new_media, dict):
                continue
            old_schema = old_media.get("schema")
            new_schema = new_media.get("schema")
            if isinstance(old_schema, dict) and isinstance(new_schema, dict):
                self._compare_schema(
                    old_schema, new_schema, base + join(media, "schema"), direction, operation
                )

    # -- security --------------------------------------------------------
    def _compare_security_schemes(self) -> None:
        old_schemes = _security_schemes(self.old.document)
        new_schemes = _security_schemes(self.new.document)
        for name in sorted(old_schemes):
            if name not in new_schemes:
                self.add(
                    "AP024",
                    "components" + join("securitySchemes", name),
                    f"security scheme {name!r} was removed",
                    "keep the scheme registered until every operation stops referencing it",
                )
                continue
            if old_schemes[name] != new_schemes[name]:
                self.add(
                    "AP032",
                    "components" + join("securitySchemes", name),
                    f"security scheme {name!r} was redefined",
                    "add a new scheme name instead of changing the existing one",
                )

    def _compare_operation_security(
        self, base: str, operation: str, old_op: dict[str, Any], new_op: dict[str, Any]
    ) -> None:
        old_security = old_op.get("security")
        new_security = new_op.get("security")
        if old_security is None and new_security is None:
            return
        old_count = len(old_security or [])
        new_count = len(new_security or [])
        if old_count and not new_count:
            self.add(
                "AP035",
                base + "/security",
                "operation no longer requires authentication",
                "loosening is safe for clients but is usually a security bug: confirm it",
                operation,
            )
        elif new_count > old_count:
            self.add(
                "AP025",
                base + "/security",
                "operation now requires additional credentials",
                "callers without the new scheme will start receiving 401/403",
                operation,
            )

    # -- schemas ---------------------------------------------------------
    def _compare_schema(
        self,
        old: dict[str, Any],
        new: dict[str, Any],
        location: str,
        direction: str,
        operation: str,
    ) -> None:
        if old == new:
            return
        self._compare_types(old, new, location, operation)
        self._compare_enum(old, new, location, operation)
        self._compare_constraints(old, new, location, operation)
        self._compare_meta(old, new, location, operation)
        self._compare_composition(old, new, location, direction, operation)
        self._compare_object(old, new, location, direction, operation)
        self._compare_items(old, new, location, direction, operation)

    def _compare_types(
        self, old: dict[str, Any], new: dict[str, Any], location: str, operation: str
    ) -> None:
        old_type = old.get("type")
        new_type = new.get("type")
        if not old_type or not new_type or old_type == new_type:
            return
        if {old_type, new_type} == {"integer", "number"} and old_type == "integer":
            self.add(
                "AP015",
                location + "/type",
                "type widened from integer to number",
                "clients that parse integers strictly may now receive 1.5",
                operation,
            )
            return
        self.add(
            "AP014",
            location + "/type",
            f"type changed from {old_type!r} to {new_type!r}",
            f"accept both {old_type} and {new_type} during a deprecation window",
            operation,
        )

    def _compare_enum(
        self, old: dict[str, Any], new: dict[str, Any], location: str, operation: str
    ) -> None:
        old_enum = old.get("enum")
        new_enum = new.get("enum")
        if not isinstance(old_enum, list) or not isinstance(new_enum, list):
            return
        old_values = [value for value in old_enum if value not in new_enum]
        new_values = [value for value in new_enum if value not in old_enum]
        if old_values:
            self.add(
                "AP016",
                location + "/enum",
                f"enum values removed: {_render_values(old_values)}",
                "clients still send these values; keep them accepted or bump the major version",
                operation,
            )
        if new_values:
            self.add(
                "AP017",
                location + "/enum",
                f"enum values added: {_render_values(new_values)}",
                "exhaustive switch statements in clients will not handle the new values",
                operation,
            )

    def _compare_constraints(
        self, old: dict[str, Any], new: dict[str, Any], location: str, operation: str
    ) -> None:
        for keyword, direction, label in _NARROWING:
            if keyword not in old or keyword not in new:
                if keyword in new and keyword not in old:
                    self.add(
                        "AP026",
                        location + "/" + keyword,
                        f"constraint added: {keyword}={new[keyword]!r}",
                        "relax the constraint or announce a major release",
                        operation,
                    )
                continue
            old_value = old[keyword]
            new_value = new[keyword]
            if old_value == new_value:
                continue
            narrowed = new_value > old_value if direction == "up" else new_value < old_value
            self.add(
                "AP026" if narrowed else "AP027",
                location + "/" + keyword,
                f"{label}: {old_value!r} -> {new_value!r}",
                "relax the bound back" if narrowed else "no client change needed",
                operation,
            )
        old_pattern = old.get("pattern")
        new_pattern = new.get("pattern")
        if old_pattern != new_pattern and new_pattern is not None:
            self.add(
                "AP026",
                location + "/pattern",
                f"pattern changed from {old_pattern!r} to {new_pattern!r}",
                "keep accepting the old pattern for one release",
                operation,
            )
        old_multiple = old.get("multipleOf")
        new_multiple = new.get("multipleOf")
        if old_multiple != new_multiple and new_multiple is not None:
            self.add(
                "AP026",
                location + "/multipleOf",
                f"multipleOf changed from {old_multiple!r} to {new_multiple!r}",
                "validate the value against the new divisor before shipping",
                operation,
            )

    def _compare_meta(
        self, old: dict[str, Any], new: dict[str, Any], location: str, operation: str
    ) -> None:
        if "default" in old and "default" in new and old["default"] != new["default"]:
            self.add(
                "AP028",
                location + "/default",
                f"default changed from {old['default']!r} to {new['default']!r}",
                "clients relying on the default will silently change behaviour",
                operation,
            )
        old_format = old.get("format")
        new_format = new.get("format")
        if old_format and new_format and old_format != new_format:
            self.add(
                "AP029",
                location + "/format",
                f"format changed from {old_format!r} to {new_format!r}",
                "code generators map formats to different types; verify the clients",
                operation,
            )
        old_extra = old.get("additionalProperties")
        new_extra = new.get("additionalProperties")
        if old_extra is not False and new_extra is False:
            self.add(
                "AP030",
                location + "/additionalProperties",
                "additionalProperties changed to false",
                "clients sending extra fields will now fail validation",
                operation,
            )
        elif old_extra is False and new_extra is not False:
            self.add(
                "AP027",
                location + "/additionalProperties",
                "additionalProperties restriction was lifted",
                "no client change needed",
                operation,
            )

    def _compare_composition(
        self,
        old: dict[str, Any],
        new: dict[str, Any],
        location: str,
        direction: str,
        operation: str,
    ) -> None:
        for keyword in ("oneOf", "anyOf"):
            old_variants = old.get(keyword)
            new_variants = new.get(keyword)
            if not isinstance(old_variants, list) or not isinstance(new_variants, list):
                continue
            old_sigs = {_signature(item) for item in old_variants}
            new_sigs = {_signature(item) for item in new_variants}
            if not old_sigs and not new_sigs:
                continue
            for removed in sorted(old_sigs - new_sigs):
                self.add(
                    "AP031",
                    location + "/" + keyword,
                    f"{keyword} variant removed: {removed}",
                    "keep the variant accepted or bump the major version",
                    operation,
                )
            for added in sorted(new_sigs - old_sigs):
                self.add(
                    "AP041",
                    location + "/" + keyword,
                    f"{keyword} variant added: {added}",
                    "clients with exhaustive handling may reject the new shape",
                    operation,
                )
        old_mapping = old.get("discriminator", {}).get("mapping") if isinstance(
            old.get("discriminator"), dict
        ) else None
        new_mapping = new.get("discriminator", {}).get("mapping") if isinstance(
            new.get("discriminator"), dict
        ) else None
        if isinstance(old_mapping, dict) and isinstance(new_mapping, dict):
            for key in sorted(old_mapping):
                if old_mapping[key] != new_mapping.get(key):
                    self.add(
                        "AP032",
                        location + "/discriminator/mapping/" + escape(key),
                        f"discriminator mapping {key!r} changed from "
                        f"{old_mapping[key]!r} to {new_mapping.get(key)!r}",
                        "routers selecting by discriminator will send traffic to the wrong schema",
                        operation,
                    )

    def _compare_object(
        self,
        old: dict[str, Any],
        new: dict[str, Any],
        location: str,
        direction: str,
        operation: str,
    ) -> None:
        old_props = old.get("properties")
        new_props = new.get("properties")
        if not isinstance(old_props, dict) or not isinstance(new_props, dict):
            return
        old_required = set(old.get("required") or [])
        new_required = set(new.get("required") or [])
        removed = [name for name in old_props if name not in new_props]
        added = [name for name in new_props if name not in old_props]

        renamed: dict[str, str] = {}
        for old_name in sorted(removed):
            for new_name in sorted(added):
                if new_name in renamed.values():
                    continue
                if _signature(old_props[old_name]) == _signature(new_props[new_name]):
                    renamed[old_name] = new_name
                    break

        for name, replacement in sorted(renamed.items()):
            self.add(
                "AP013",
                location + join("properties", replacement),
                f"property {name!r} appears to be renamed to {replacement!r}",
                "keep the old property as a deprecated alias for one major version",
                operation,
            )
        for name in sorted(removed):
            if name in renamed:
                continue
            code = "AP012" if direction == "request" else "AP011"
            self.add(
                code,
                location + join("properties", name),
                f"property {name!r} was removed from a {direction}",
                "keep returning/sending it until the next major version",
                operation,
            )
        for name in sorted(added):
            if name in renamed.values():
                continue
            required = name in new_required
            if direction == "request" and required:
                self.add(
                    "AP010",
                    location + join("properties", name),
                    f"new required property {name!r} in a request body",
                    "make it optional with a default, or bump the major version",
                    operation,
                )
            elif direction == "response" and required:
                self.add(
                    "AP038",
                    location + join("properties", name),
                    f"new required property {name!r} in a response",
                    "clients can ignore unknown fields; no action for the server",
                    operation,
                )
            else:
                self.add(
                    "AP037",
                    location + join("properties", name),
                    f"property {name!r} was added",
                    "additive change: document it in the changelog",
                    operation,
                )
        for name in sorted(new_required - old_required):
            if name not in new_props or name in added:
                continue
            if direction == "request":
                self.add(
                    "AP018",
                    location + join("properties", name) + "/required",
                    f"property {name!r} is now required",
                    "accept the omission for one release and log it",
                    operation,
                )
            else:
                self.add(
                    "AP019",
                    location + join("properties", name) + "/required",
                    f"property {name!r} became required in a response",
                    "clients that relied on its absence may misbehave",
                    operation,
                )
        for name in sorted(old_required - new_required):
            if name not in old_props or name in removed:
                continue
            self.add(
                "AP019" if direction == "request" else "AP038",
                location + join("properties", name) + "/required",
                f"property {name!r} is no longer required",
                "no client change needed; keep sending it if useful",
                operation,
            )
        for name in sorted(set(old_props) & set(new_props)):
            old_child = old_props[name]
            new_child = new_props[name]
            if isinstance(old_child, dict) and isinstance(new_child, dict):
                self._compare_schema(
                    old_child,
                    new_child,
                    location + join("properties", name),
                    direction,
                    operation,
                )

    def _compare_items(
        self,
        old: dict[str, Any],
        new: dict[str, Any],
        location: str,
        direction: str,
        operation: str,
    ) -> None:
        old_items = old.get("items")
        new_items = new.get("items")
        if isinstance(old_items, dict) and isinstance(new_items, dict):
            self._compare_schema(old_items, new_items, location + "/items", direction, operation)
        old_extra = old.get("additionalProperties")
        new_extra = new.get("additionalProperties")
        if isinstance(old_extra, dict) and isinstance(new_extra, dict):
            self._compare_schema(
                old_extra, new_extra, location + "/additionalProperties", direction, operation
            )


def _parameters_by_key(raw: Any) -> dict[tuple[str, str], dict[str, Any]]:
    found: dict[tuple[str, str], dict[str, Any]] = {}
    if not isinstance(raw, list):
        return found
    for parameter in raw:
        if not isinstance(parameter, dict):
            continue
        name = parameter.get("name")
        where = parameter.get("in")
        if isinstance(name, str) and isinstance(where, str):
            found[(name, where)] = parameter
    return found


def _schema_from_content(content: Any) -> Any:
    if not isinstance(content, dict) or not content:
        return None
    first = next(iter(content.values()))
    if isinstance(first, dict):
        return first.get("schema")
    return None


def _security_schemes(document: dict[str, Any]) -> dict[str, Any]:
    components = document.get("components")
    if not isinstance(components, dict):
        return {}
    schemes = components.get("securitySchemes")
    return schemes if isinstance(schemes, dict) else {}


def _signature(schema: Any) -> str:
    import json

    return json.dumps(schema, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _render_values(values: Iterable[Any]) -> str:
    return ", ".join(repr(value) for value in values)


def load_findings(result: DiffResult) -> list[Finding]:
    """Return findings sorted by severity then document location."""
    return sorted(
        result.findings,
        key=lambda item: (-_RANK[item.severity], item.operation, item.location, item.code),
    )
