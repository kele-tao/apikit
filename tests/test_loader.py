"""Tests for spec loading, JSON pointers and validation helpers."""

from __future__ import annotations

import json

import pytest

from apikit.loader import Spec, SpecError, check_supported, load_document
from apikit.pointer import PointerError, escape, join, resolve, split, unescape
from apikit.validate import coerce_param, validate_value


def test_escape_and_unescape_round_trip():
    assert escape("a/b~c") == "a~1b~0c"
    assert unescape("a~1b~0c") == "a/b~c"
    assert split(join("paths", "/pets/{petId}")) == ["paths", "/pets/{petId}"]


def test_resolve_walks_objects_and_arrays(v1_document):
    assert resolve(v1_document, "/info/version") == "1.4.0"
    assert resolve(v1_document, "/components/schemas/Tag/enum/0") == "dog"


def test_resolve_reports_missing_pointer(v1_document):
    with pytest.raises(PointerError):
        resolve(v1_document, "/info/missing")
    with pytest.raises(PointerError):
        resolve(v1_document, "/components/schemas/Tag/enum/99")
    with pytest.raises(PointerError):
        resolve({"a": 1}, "/a/b")


def test_split_rejects_invalid_pointer():
    with pytest.raises(PointerError):
        split("info/version")


def test_load_document_rejects_non_object(tmp_path):
    path = tmp_path / "list.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(SpecError):
        load_document(path)


def test_load_document_reports_directory(tmp_path):
    with pytest.raises(SpecError):
        load_document(tmp_path)


def test_load_yaml_without_extra(tmp_path, monkeypatch):
    path = tmp_path / "spec.yaml"
    path.write_text("openapi: 3.0.0\ninfo:\n  title: T\n  version: 1.0.0\npaths: {}\n",
                    encoding="utf-8")
    document = load_document(path)
    assert document["openapi"] == "3.0.0"


def test_yaml_error_when_pyyaml_missing(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("no yaml today")
        return real_import(name, *args, **kwargs)

    path = tmp_path / "spec.yaml"
    path.write_text("openapi: 3.0.0\n", encoding="utf-8")
    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(SpecError) as excinfo:
        load_document(path)
    assert "apikit[yaml]" in str(excinfo.value)


def test_check_supported_rejects_swagger_2():
    with pytest.raises(SpecError):
        check_supported({"swagger": "2.0"})


def test_check_supported_requires_a_version():
    with pytest.raises(SpecError):
        check_supported({})


def test_spec_dereferences_local_refs(v1_document):
    spec = Spec(v1_document)
    operation = next(
        item for item in spec.operations() if item[2].get("operationId") == "createPet"
    )
    schema = operation[2]["requestBody"]["content"]["application/json"]["schema"]
    assert "properties" in schema
    assert "$ref" not in json.dumps(schema)


def test_spec_tolerates_reference_cycles():
    document = {
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1"},
        "paths": {},
        "components": {
            "schemas": {
                "Node": {
                    "type": "object",
                    "properties": {"child": {"$ref": "#/components/schemas/Node"}},
                }
            }
        },
    }
    spec = Spec(document)
    resolved = spec.deref({"$ref": "#/components/schemas/Node"})
    assert resolved["properties"]["child"]["type"] == "object"


def test_spec_operations_merge_path_level_parameters(v1_document):
    spec = Spec(v1_document)
    operation = next(item for item in spec.operations() if item[2].get("operationId") == "getPet")
    names = {parameter["name"] for parameter in operation[2]["parameters"]}
    assert "petId" in names


def test_coerce_param():
    assert coerce_param("42", {"type": "integer"}) == (42, None)
    assert coerce_param("1.5", {"type": "number"}) == (1.5, None)
    assert coerce_param("true", {"type": "boolean"})[0] is True
    value, issue = coerce_param("nope", {"type": "integer"})
    assert issue is not None and "expected integer" in issue.message
    assert coerce_param("a,b", {"type": "array"})[0] == ["a", "b"]


def test_validate_value_additional_properties():
    schema = {"type": "object", "additionalProperties": False, "properties": {"a": {}}}
    issues = validate_value({"a": 1, "b": 2}, schema)
    assert issues[0].message == "unexpected property 'b'"


def test_validate_value_composition():
    schema = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
    assert validate_value("x", schema) == []
    issues = validate_value(1.5, schema)
    assert "oneOf" in issues[0].message


def test_validate_value_multiple_of_and_bounds():
    schema = {"type": "number", "multipleOf": 3, "minimum": 0, "maximum": 10}
    assert validate_value(9, schema) == []
    assert any("multiple" in issue.message for issue in validate_value(8, schema))
    assert any("below minimum" in issue.message for issue in validate_value(-3, schema))
