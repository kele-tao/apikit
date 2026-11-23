"""Shared fixtures for the apikit test suite."""

from __future__ import annotations

import copy
import json
import pathlib

import pytest

EXAMPLES = pathlib.Path(__file__).resolve().parents[1] / "examples"
V1 = EXAMPLES / "petstore.v1.json"
V2 = EXAMPLES / "petstore.v2.json"


@pytest.fixture()
def v1_document() -> dict:
    return json.loads(V1.read_text(encoding="utf-8"))


@pytest.fixture()
def v2_document() -> dict:
    return json.loads(V2.read_text(encoding="utf-8"))


@pytest.fixture()
def v1_spec_path() -> pathlib.Path:
    return V1


@pytest.fixture()
def clone():
    """Deep-copy a document so a test can mutate it safely."""

    def _clone(document: dict) -> dict:
        return copy.deepcopy(document)

    return _clone


def minimal_spec(**overrides) -> dict:
    """A tiny valid OpenAPI document used as the base for focused tests."""
    document = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1.0.0"},
        "paths": {
            "/items": {
                "get": {
                    "operationId": "listItems",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    document.update(overrides)
    return document
