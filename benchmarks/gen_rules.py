"""Generate docs/RULES.md from the rule registry in apikit.diff.

Keeping the documentation generated means a rule cannot exist without a
description, and the README table always matches the code.

  python benchmarks/gen_rules.py
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from apikit.diff import RULES  # noqa: E402

HEADER = """# Rule reference

Every finding apikit reports carries one of the rule codes below. The table is
generated from `src/apikit/diff.py` by `python benchmarks/gen_rules.py`, so it
cannot drift away from the implementation.

| Code | Severity | Meaning |
| --- | --- | --- |
"""


def main() -> int:
    rows = [
        f"| {code} | {severity} | {text} |"
        for code, (severity, text) in sorted(RULES.items())
    ]
    counts = {"breaking": 0, "warning": 0, "info": 0}
    for severity, _ in RULES.values():
        counts[severity] += 1
    summary = (
        f"\nTotal: **{len(RULES)} rules** ({counts['breaking']} breaking, "
        f"{counts['warning']} warning, {counts['info']} info).\n"
    )
    target = ROOT / "docs" / "RULES.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(HEADER + "\n".join(rows) + "\n" + summary, encoding="utf-8")
    print("wrote", target, "with", len(RULES), "rules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
