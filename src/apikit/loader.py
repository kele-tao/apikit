"""Load OpenAPI documents from JSON (native) or YAML (optional extra)."""

from __future__ import annotations

import json
import pathlib
from typing import Any

from .pointer import SpecError, resolve

_HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


def load_document(path: str | pathlib.Path) -> dict[str, Any]:
    """Read a spec file and return its document.

    JSON always works; a YAML document needs the optional pyyaml extra.
    """
    file_path = pathlib.Path(path)
    try:
        raw = file_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SpecError(f"spec not found: {file_path}") from exc
    except IsADirectoryError as exc:
        raise SpecError(f"{file_path} is a directory, not a spec file") from exc
    except OSError as exc:
        raise SpecError(f"cannot read {file_path}: {exc}") from exc

    if not raw.strip():
        raise SpecError(f"{file_path} is empty")

    if file_path.suffix.lower() in {".yaml", ".yml"}:
        document = _load_yaml(raw, file_path)
    else:
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SpecError(
                f"{file_path} is not valid JSON at line {exc.lineno}, "
                f"column {exc.colno}: {exc.msg}"
            ) from exc

    if not isinstance(document, dict):
        raise SpecError(f"{file_path} must contain a JSON object at the top level")
    return document


def _load_yaml(raw: str, file_path: pathlib.Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise SpecError(
            f"{file_path} is YAML; install the optional extra first: pip install 'apikit[yaml]'"
        ) from exc
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise SpecError(f"{file_path} is not valid YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise SpecError(f"{file_path} must contain a mapping at the top level")
    return document


def version_of(document: dict[str, Any]) -> str:
    """Return the OpenAPI/Swagger version string of a document."""
    version = document.get("openapi") or document.get("swagger")
    if not isinstance(version, str):
        raise SpecError("document has no 'openapi' or 'swagger' version field")
    return version


def check_supported(document: dict[str, Any]) -> str:
    """Validate that a document is OpenAPI 3.x, returning its version string."""
    version = version_of(document)
    if not version.startswith("3."):
        raise SpecError(
            f"unsupported spec version {version!r}: apikit reads OpenAPI 3.x "
            "(convert Swagger 2.0 with swagger2openapi first)"
        )
    return version


class Spec:
    """An OpenAPI document plus local and relative-file $ref dereferencing."""

    def __init__(self, document: dict[str, Any], base_dir: pathlib.Path | None = None) -> None:
        self.document = document
        self.version = check_supported(document)
        self.base_dir = base_dir or pathlib.Path.cwd()
        self._external: dict[pathlib.Path, dict[str, Any]] = {}

    @classmethod
    def from_file(cls, path: str | pathlib.Path) -> Spec:
        file_path = pathlib.Path(path)
        return cls(load_document(file_path), file_path.parent)

    def paths(self) -> dict[str, Any]:
        """Return the normalised paths object."""
        paths = self.document.get("paths") or {}
        if not isinstance(paths, dict):
            raise SpecError("'paths' must be an object")
        return {str(path): item for path, item in paths.items() if isinstance(item, dict)}

    def operations(self) -> list[tuple[str, str, dict[str, Any]]]:
        """Return (path, method, operation) for every operation in the spec."""
        found: list[tuple[str, str, dict[str, Any]]] = []
        for path, item in self.paths().items():
            shared = item.get("parameters") or []
            for method in _HTTP_METHODS:
                operation = item.get(method)
                if not isinstance(operation, dict):
                    continue
                merged = dict(operation)
                merged["parameters"] = self._merge_parameters(shared, operation.get("parameters"))
                found.append((path, method, self.deref(merged)))
        return found

    def _merge_parameters(self, shared: Any, own: Any) -> list[Any]:
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

    def deref(self, node: Any, _seen: frozenset[str] = frozenset()) -> Any:
        """Recursively resolve $ref pointers, tolerating cycles."""
        if isinstance(node, list):
            return [self.deref(item, _seen) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str):
            target = self._resolve_ref(ref, _seen)
            if target is not _UNRESOLVED:
                return target
            return {"x-apikit-unresolved": ref}
        return {key: self.deref(value, _seen) for key, value in node.items()}

    def _resolve_ref(self, ref: str, seen: frozenset[str]) -> Any:
        from .pointer import split_uri_fragment

        document, pointer = split_uri_fragment(ref)
        if ref in seen:
            return {"type": "object", "x-apikit-cycle": ref}
        try:
            target = resolve(self._document_for(document), pointer)
        except Exception:  # noqa: BLE001 - unresolvable refs surface as a marker
            return _UNRESOLVED
        return self.deref(target, seen | {ref})

    def _document_for(self, document: str) -> dict[str, Any]:
        if not document:
            return self.document
        target = (self.base_dir / document).resolve()
        if target not in self._external:
            try:
                self._external[target] = load_document(target)
            except SpecError:
                self._external[target] = {}
        return self._external[target]


class _Unresolved:
    """Sentinel returned when a $ref cannot be resolved."""


_UNRESOLVED = _Unresolved()
