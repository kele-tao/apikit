"""apikit: OpenAPI compatibility diffing, deterministic mocking, request signing.

The public surface is intentionally small so CI users never have to shell out::

    from apikit import diff_specs, load_document

    result = diff_specs(load_document("old.json"), load_document("new.json"))
    if result.breaking:
        raise SystemExit(str(len(result.breaking)) + " breaking changes: bump major")
"""

from __future__ import annotations

from .diff import RULES, DiffResult, Finding, bump_version, diff_specs
from .generate import GenerationError, generate_example
from .loader import Spec, SpecError, load_document
from .mock import MockOptions, create_server
from .pointer import ApikitError, PointerError
from .signer import SigningError, VerifyResult, canonical_string, sign_request, verify_request
from .validate import Issue, validate_value

__version__ = "1.0.0"

__all__ = [
    "RULES",
    "ApikitError",
    "DiffResult",
    "Finding",
    "GenerationError",
    "Issue",
    "MockOptions",
    "PointerError",
    "Spec",
    "SpecError",
    "SigningError",
    "VerifyResult",
    "__version__",
    "bump_version",
    "canonical_string",
    "create_server",
    "diff_specs",
    "generate_example",
    "load_document",
    "sign_request",
    "validate_value",
    "verify_request",
]
