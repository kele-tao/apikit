# Roadmap and deliberate omissions

This is the honest list of what v1.0 does **not** do. Nothing here is implemented
behind a placeholder flag; if a feature is listed, it genuinely does not exist yet.

## Cut from v1.0

* **YAML-first workflows.** YAML specs load through the optional `pyyaml` extra,
  but the shipped examples, the test suite and the CI job are JSON-only so the tool
  stays dependency-free by default. A conformance fixture set of YAML specs is the
  natural next step.
* **`$ref` across the network.** Local pointers and relative files resolve;
  `http(s)://` refs are reported as unresolved and skipped, because resolving them
  would break the offline promise and introduce a supply-chain hole in CI.
* **Full JSON Schema 2020-12 validation.** The mock enforces the keywords that
  matter for request checking (types, required, enum, const, bounds, pattern,
  format, items, additionalProperties, allOf/oneOf/anyOf/not). `if/then/else`,
  `dependentRequired`, `unevaluatedProperties`, recursive anchor references and
  content encodings are not implemented.
* **Mock response selection by status code.** The server picks the first successful
  response declared by the operation. Forcing a specific status (for example to test
  a 404 path) needs a query override that does not exist yet.
* **`validate --requests replay`.** The brief asked for replaying a recorded request
  log against the spec. The validator exists and is exercised by the mock server on
  every request, but the offline replay command was cut to keep v1.0 honest and
  small. Request logs are still produced (the mock's access log format).
* **`--record` fixture writing.** The mock does not write fixture files; use
  `curl` against the running server, which is what the README shows.

## Next

1. **`validate --requests log.jsonl`** - replay a captured request log against the
   spec and report contract violations. The validator and the request model already
   exist, so this is a CLI surface plus a fixture format.
2. **Status forcing** - `X-Apikit-Status: 404` and `?__status=404` so a client team can
   exercise error paths without editing the spec.
3. **Rule severity overrides** - a `.apikit.json` config mapping rule codes to
   `off`/`warning`/`breaking`, because every team has one rule it disagrees with.
4. **OpenAPI 3.1 `$ref` siblings and `jsonSchemaDialect`** - 3.1 documents load, but
   the ref-sibling semantics are not modelled yet.
5. **`diff --ignore AP0xx`** - suppress a rule for a single CI job without editing
   the engine.
