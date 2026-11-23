"""Tests for the breaking-change detector."""

from __future__ import annotations

import json

import pytest

from apikit import diff_specs
from apikit.diff import RULES, bump_version, diff_documents
from apikit.loader import Spec, SpecError


def codes(result) -> list[str]:
    return sorted(item.code for item in result.findings)


def test_example_specs_detect_breaking_changes(v1_document, v2_document):
    result = diff_specs(v1_document, v2_document)
    assert len(result.breaking) >= 3
    assert {"AP001", "AP003", "AP010", "AP014", "AP016", "AP024"} <= set(codes(result))
    assert result.suggested_bump == "major"


def test_example_specs_detect_non_breaking_changes(v1_document, v2_document):
    result = diff_specs(v1_document, v2_document)
    assert "AP002" in codes(result)
    assert "AP006" in codes(result)
    assert "AP041" in codes(result)
    assert len(result.info) >= 4


def test_identical_specs_produce_no_findings(v1_document):
    result = diff_specs(v1_document, v1_document)
    assert result.findings == []
    assert result.suggested_bump == "none"
    assert result.suggested_version == "1.4.0"


def test_findings_have_pointer_locations_and_fixes(v1_document, v2_document):
    result = diff_specs(v1_document, v2_document)
    for finding in result.findings:
        assert finding.location.startswith("paths/") or finding.location.startswith("components/")
        assert finding.fix
        assert finding.message
        assert finding.severity in {"breaking", "warning", "info"}


def test_json_pointer_escapes_slashes(v1_document, v2_document):
    result = diff_specs(v1_document, v2_document)
    removed = next(item for item in result.findings if item.code == "AP001")
    assert removed.location == "paths/~1store~1inventory"


def test_new_required_parameter_is_breaking(v1_document, clone):
    new = clone(v1_document)
    new["paths"]["/pets"]["get"]["parameters"].append(
        {"name": "tenant", "in": "query", "required": True, "schema": {"type": "string"}}
    )
    result = diff_specs(v1_document, new)
    assert "AP020" in codes(result)
    assert result.suggested_bump == "major"


def test_new_optional_parameter_is_info(v1_document, clone):
    new = clone(v1_document)
    new["paths"]["/pets"]["get"]["parameters"].append(
        {"name": "cursor", "in": "query", "required": False, "schema": {"type": "string"}}
    )
    result = diff_specs(v1_document, new)
    assert "AP041" in codes(result)
    assert result.findings[0].severity == "info"


def test_optional_to_required_property_is_breaking(v1_document, clone):
    new = clone(v1_document)
    new["components"]["schemas"]["Order"]["required"].append("shipDate")
    result = diff_specs(v1_document, new)
    assert "AP018" in codes(result)


def test_required_to_optional_property_is_info(v1_document, clone):
    new = clone(v1_document)
    new["components"]["schemas"]["Order"]["required"].remove("quantity")
    result = diff_specs(v1_document, new)
    assert "AP019" in codes(result)
    assert all(item.severity in {"info", "warning"} for item in result.findings)


def test_enum_tightening_and_widening(v1_document, clone):
    tightened = clone(v1_document)
    tightened["components"]["schemas"]["Tag"]["enum"] = ["dog"]
    result = diff_specs(v1_document, tightened)
    assert "AP016" in codes(result)

    widened = clone(v1_document)
    widened["components"]["schemas"]["Tag"]["enum"] = ["dog", "cat", "bird", "fish"]
    result = diff_specs(v1_document, widened)
    assert "AP017" in codes(result)
    assert "AP016" not in codes(result)


def test_numeric_widening_integer_to_number_is_info(v1_document, clone):
    new = clone(v1_document)
    new["components"]["schemas"]["Order"]["properties"]["quantity"]["type"] = "number"
    result = diff_specs(v1_document, new)
    assert "AP015" in codes(result)
    assert "AP014" not in codes(result)


def test_constraint_narrowing_and_loosening(v1_document, clone):
    narrowed = clone(v1_document)
    narrowed["components"]["schemas"]["Pet"]["properties"]["name"]["minLength"] = 5
    result = diff_specs(v1_document, narrowed)
    assert "AP026" in codes(result)

    loosened = clone(v1_document)
    loosened["components"]["schemas"]["Pet"]["properties"]["name"]["maxLength"] = 200
    result = diff_specs(v1_document, loosened)
    assert "AP027" in codes(result)


def test_default_change_is_a_warning(v1_document, clone):
    new = clone(v1_document)
    new["components"]["schemas"]["Order"]["properties"]["quantity"]["default"] = 7
    result = diff_specs(v1_document, new)
    assert "AP028" in codes(result)
    assert result.suggested_bump in {"major", "minor"}


def test_property_rename_is_detected(v1_document, clone):
    new = clone(v1_document)
    properties = new["components"]["schemas"]["Order"]["properties"]
    properties["pet_id"] = properties.pop("petId")
    new["components"]["schemas"]["Order"]["required"] = ["pet_id", "quantity"]
    result = diff_specs(v1_document, new)
    assert "AP013" in codes(result)


def test_security_requirement_tightening(v1_document, clone):
    new = clone(v1_document)
    new["paths"]["/pets"]["get"]["security"] = [{"ApiKeyAuth": []}, {"OAuth2": []}]
    result = diff_specs(v1_document, new)
    assert "AP025" in codes(result)


def test_removed_response_and_media_type(v1_document, clone):
    new = clone(v1_document)
    del new["paths"]["/pets"]["post"]["responses"]["422"]
    result = diff_specs(v1_document, new)
    assert "AP005" in codes(result)

    media = clone(v1_document)
    media["paths"]["/pets"]["get"]["responses"]["200"]["content"] = {
        "application/xml": {"schema": {"type": "string"}}
    }
    result = diff_specs(v1_document, media)
    assert "AP040" in codes(result)


def test_operation_deprecation_and_rename(v1_document, clone):
    new = clone(v1_document)
    new["paths"]["/pets"]["get"]["deprecated"] = True
    new["paths"]["/pets"]["get"]["operationId"] = "listAllPets"
    result = diff_specs(v1_document, new)
    assert {"AP033", "AP034"} <= set(codes(result))


def test_oneof_variant_removal(v1_document, clone):
    old = clone(v1_document)
    old["components"]["schemas"]["Order"]["properties"]["note"] = {
        "oneOf": [{"type": "string"}, {"type": "integer"}]
    }
    new = clone(old)
    new["components"]["schemas"]["Order"]["properties"]["note"]["oneOf"] = [{"type": "string"}]
    result = diff_specs(old, new)
    assert "AP031" in codes(result)


def test_removed_path_and_added_path(v1_document, clone):
    new = clone(v1_document)
    del new["paths"]["/store/orders"]
    new["paths"]["/health"] = {"get": {"responses": {"200": {"description": "ok"}}}}
    result = diff_specs(v1_document, new)
    assert "AP001" in codes(result)
    assert "AP002" in codes(result)


def test_every_rule_has_a_documented_severity():
    assert len(RULES) >= 15
    for code, (severity, text) in RULES.items():
        assert code.startswith("AP")
        assert severity in {"breaking", "warning", "info"}
        assert text


def test_bump_version():
    assert bump_version("1.4.0", "major") == "2.0.0"
    assert bump_version("1.4.0", "minor") == "1.5.0"
    assert bump_version("1.4.0", "patch") == "1.4.1"
    assert bump_version("1.4", "patch") == "1.4.1"
    assert bump_version("1.4.0", "none") == "1.4.0"
    assert bump_version("not-a-version", "major") == "not-a-version"


def test_result_serialises_to_json(v1_document, v2_document):
    result = diff_specs(v1_document, v2_document)
    payload = result.to_dict()
    text = json.dumps(payload, sort_keys=True)
    restored = json.loads(text)
    assert restored["counts"]["breaking"] == len(result.breaking)
    assert restored["suggested_bump"] == "major"
    assert restored["new"]["version"] == "1.5.0"


def test_empty_paths_are_equal():
    empty = {"openapi": "3.0.0", "info": {"title": "t", "version": "1.0.0"}, "paths": {}}
    result = diff_specs(empty, empty)
    assert result.findings == []
    assert result.old_operations == 0


def test_unsupported_version_is_a_spec_error():
    with pytest.raises(SpecError):
        Spec({"swagger": "2.0", "info": {}, "paths": {}})


def test_operation_removed_is_breaking(v1_document, clone):
    new = clone(v1_document)
    del new["paths"]["/pets/{petId}"]["delete"]
    result = diff_specs(v1_document, new)
    finding = next(item for item in result.findings if item.code == "AP003")
    assert finding.severity == "breaking"
    assert "DELETE" in finding.message


def test_deterministic_findings_order(v1_document, v2_document):
    first = diff_documents(v1_document, v2_document).to_dict()
    second = diff_documents(v1_document, v2_document).to_dict()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
