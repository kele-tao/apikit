# Contributing

Thanks for taking the time to improve apikit.

## Development setup

```bash
git clone https://github.com/kele-tao/apikit.git
cd apikit
python -m venv .venv
. .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q
ruff check .
```

## What a good pull request looks like

1. One behaviour change per PR, with a test that fails before the change.
2. New diff rules need three things: a stable rule code (next free `AP0xx`),
   a row in the README rule table, and a test that proves the severity.
3. No new runtime dependencies. apikit is stdlib-only by design; `pyyaml` is
   the single optional extra and must stay optional.
4. Run the local gate before pushing:

```bash
pytest -q
ruff check .
python -m apikit diff examples/petstore.v1.json examples/petstore.v2.json
```

## Reporting bugs

Use the issue template and include the spec fragment (redact secrets) plus the
exact command and the output of `apikit --version`.
