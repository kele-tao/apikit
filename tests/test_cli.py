"""End-to-end tests for the command line interface."""

from __future__ import annotations

import json

import pytest
from conftest import V1, V2, minimal_spec

from apikit.cli import main


def run(capsys, *argv) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("apikit ")


def test_help_without_command(capsys):
    code = main([])
    out = capsys.readouterr().out
    assert code == 2
    assert "diff" in out and "mock" in out and "sign" in out


def test_diff_exits_one_on_breaking(capsys):
    code, out, _ = run(capsys, "diff", str(V1), str(V2), "--no-color")
    assert code == 1
    assert "BREAKING" in out
    assert "suggested bump: major" in out


def test_diff_fail_on_none_always_exits_zero(capsys):
    code, out, _ = run(capsys, "diff", str(V1), str(V2), "--fail-on", "none", "--no-color")
    assert code == 0
    assert "verdict:" in out


def test_diff_fail_on_info_trips_on_additive_changes(capsys, tmp_path, clone):
    old = minimal_spec()
    new = clone(old)
    new["paths"]["/items"]["get"]["deprecated"] = True
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    old_path.write_text(json.dumps(old), encoding="utf-8")
    new_path.write_text(json.dumps(new), encoding="utf-8")
    code, _, _ = run(capsys, "diff", str(old_path), str(new_path), "--fail-on", "warning")
    assert code == 1


def test_diff_json_format_is_machine_readable(capsys):
    code, out, _ = run(capsys, "diff", str(V1), str(V2), "--format", "json")
    payload = json.loads(out)
    assert code == 1
    assert payload["suggested_bump"] == "major"
    assert payload["counts"]["breaking"] >= 3
    assert all("code" in item for item in payload["findings"])


def test_diff_markdown_format(capsys):
    code, out, _ = run(capsys, "diff", str(V1), str(V2), "--format", "md")
    assert code == 1
    assert out.startswith("## apikit compatibility report")
    assert "| severity | rule | location | finding | fix |" in out


def test_diff_reports_missing_file(capsys, tmp_path):
    code, _, err = run(capsys, "diff", str(tmp_path / "nope.json"), str(V1))
    assert code == 2
    assert "spec not found" in err


def test_diff_reports_invalid_json(capsys, tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    code, _, err = run(capsys, "diff", str(broken), str(V1))
    assert code == 2
    assert "not valid JSON" in err


def test_diff_reports_empty_file(capsys, tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text("   ", encoding="utf-8")
    code, _, err = run(capsys, "diff", str(empty), str(V1))
    assert code == 2
    assert "empty" in err


def test_diff_identical_specs_exit_zero(capsys):
    code, out, _ = run(capsys, "diff", str(V1), str(V1), "--no-color")
    assert code == 0
    assert "no changes detected" in out


def test_examples_command(capsys):
    code, out, _ = run(capsys, "examples", str(V1))
    assert code == 0
    assert "curl -sS -X GET" in out
    assert "http GET" in out
    assert "urllib.request" in out
    assert "/pets/1" in out


def test_examples_json(capsys):
    code, out, _ = run(capsys, "examples", str(V1), "--format", "json")
    payload = json.loads(out)
    assert code == 0
    assert len(payload) == 6
    assert payload[0]["operationId"] == "listPets"


def test_rules_command_lists_every_rule(capsys):
    code, out, _ = run(capsys, "rules", "--no-color")
    assert code == 0
    assert "AP001" in out
    assert "documented rules" in out


def test_rules_json(capsys):
    code, out, _ = run(capsys, "rules", "--format", "json")
    payload = json.loads(out)
    assert code == 0
    assert any(item["code"] == "AP014" for item in payload)
    assert all(item["severity"] in {"breaking", "warning", "info"} for item in payload)


def test_sign_and_verify_round_trip(capsys):
    code, out, _ = run(
        capsys, "sign", "--secret", "s3cret", "--method", "POST",
        "--path", "/orders", "--body", '{"a":1}', "--timestamp", "1700000000",
        "--nonce", "abcdefghijkl", "--format", "json",
    )
    headers = json.loads(out)
    assert code == 0
    assert headers["X-Apikit-Signature"].startswith("v1=")
    code, out, _ = run(
        capsys, "verify", "--secret", "s3cret", "--method", "POST", "--path", "/orders",
        "--body", '{"a":1}', "--timestamp", headers["X-Apikit-Timestamp"],
        "--nonce", headers["X-Apikit-Nonce"], "--signature", headers["X-Apikit-Signature"],
        "--window", "999999999",
    )
    assert code == 0
    assert "signature valid" in out


def test_verify_rejects_tampered_body(capsys):
    code, out, _ = run(
        capsys, "verify", "--secret", "s3cret", "--method", "POST", "--path", "/orders",
        "--body", '{"a":2}', "--timestamp", "1700000000", "--nonce", "abcdefghijkl",
        "--signature", "v1=" + "0" * 64, "--window", "999999999",
    )
    assert code == 1
    assert "bad_signature" in out


def test_sign_requires_a_secret(capsys, monkeypatch):
    monkeypatch.delenv("APIKIT_SECRET", raising=False)
    code, _, err = run(capsys, "sign", "--path", "/x")
    assert code == 2
    assert "no secret" in err


def test_mock_rejects_bad_error_rate(capsys, tmp_path):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(minimal_spec()), encoding="utf-8")
    code, _, err = run(capsys, "mock", str(spec), "--error-rate", "2.5")
    assert code == 2
    assert "--error-rate" in err


def test_unicode_in_spec_is_preserved(capsys, tmp_path, clone):
    old = minimal_spec()
    old["info"]["title"] = "Boutique \u732b \U0001f431"
    new = clone(old)
    new["paths"]["/items"]["get"]["deprecated"] = True
    del new["paths"]["/items"]
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    old_path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    new_path.write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
    code, out, _ = run(capsys, "diff", str(old_path), str(new_path), "--no-color")
    assert code == 1
    assert "path /items was removed" in out
