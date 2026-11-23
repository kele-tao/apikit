"""RFC 6901 JSON Pointer helpers and error types used across apikit."""

from __future__ import annotations

from typing import Any


class ApikitError(Exception):
    """Base class for every error apikit reports to the user."""


class SpecError(ApikitError):
    """The spec file is unreadable, not JSON/YAML, or structurally invalid."""


class PointerError(ApikitError):
    """A JSON pointer does not resolve inside the document."""


def escape(token: str) -> str:
    """Escape a single reference token: '~' becomes '~0' and '/' becomes '~1'."""
    return token.replace("~", "~0").replace("/", "~1")


def unescape(token: str) -> str:
    """Reverse :func:escape, turning '~1' back into '/' and '~0' into '~'."""
    return token.replace("~1", "/").replace("~0", "~")


def split(pointer: str) -> list[str]:
    """Split a JSON pointer into unescaped reference tokens."""
    if pointer in ("", "#"):
        return []
    if pointer.startswith("#"):
        pointer = pointer[1:]
    if not pointer.startswith("/"):
        raise PointerError(f"invalid JSON pointer {pointer!r}: must start with '/'")
    return [unescape(token) for token in pointer[1:].split("/")]


def join(*tokens: str) -> str:
    """Build a JSON pointer from reference tokens."""
    return "".join("/" + escape(str(token)) for token in tokens)


def resolve(document: Any, pointer: str) -> Any:
    """Resolve *pointer* against *document*, raising PointerError when it cannot."""
    node = document
    for token in split(pointer):
        if isinstance(node, list):
            try:
                node = node[int(token)]
            except (ValueError, IndexError) as exc:
                raise PointerError(f"pointer {pointer!r} does not exist: {exc}") from exc
        elif isinstance(node, dict):
            if token not in node:
                raise PointerError(f"pointer {pointer!r} does not exist: no key {token!r}")
            node = node[token]
        else:
            raise PointerError(f"cannot walk {pointer!r} through a scalar value")
    return node


def split_uri_fragment(uri: str) -> tuple[str, str]:
    """Split a $ref URI into its document part and its pointer part."""
    if "#" in uri:
        document, _, fragment = uri.partition("#")
        return document, "/" + fragment.lstrip("/") if fragment else ""
    return uri, ""
