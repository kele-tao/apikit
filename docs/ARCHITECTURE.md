# Architecture

apikit is six small modules plus a CLI. Everything below runs on the Python standard
library alone; `pyyaml` is an optional extra used only to read YAML specs.

```
src/apikit/
  pointer.py    JSON Pointer (RFC 6901) helpers and the error hierarchy
  loader.py     spec loading (JSON/YAML) and $ref dereferencing
  diff.py       the rule engine: 41 rules, severities, findings, semver verdict
  generate.py   seeded example generation from JSON Schema
  pattern.py    a small regex sampler used by "pattern"
  validate.py   the schema subset the mock server enforces
  mock.py       http.server-based router, validation and RFC 7807 errors
  signer.py     canonical string, HMAC-SHA256 signing, replay cache
  cli.py        argparse surface, three renderers (text/markdown/json)
```

## The diff engine

`diff.py` dereferences both documents first, then walks paths, operations,
parameters, request bodies, responses, media types and nested schemas. Every
incompatibility is recorded as a `Finding` with a rule code, a severity, a JSON
pointer, a one-line message and a one-line fix.

Shared schemas are the interesting case: `#/components/schemas/Pet` is dereferenced
into every operation that returns it, so a single field removal is reported once per
usage site. Findings that share a rule code **and** a message are merged, keeping the
first location and counting the rest. Without merging, a change to a popular schema
buries the report; with merging, a reader sees one issue and knows it is everywhere.

## The mock server

The router compiles each OpenAPI path template into an anchored regular expression
(`/pets/{petId}` becomes `^/pets/(?P<petId>[^/]+)$`). A request is matched, path
and query parameters are coerced to the declared types, the body is validated against
the JSON Schema subset in `validate.py`, and a response is generated from the
operation's response schema with a per-node seeded RNG.

Response generation is deterministic per seed and operation id, not per request
order, so two servers started with the same seed return byte-identical bodies. That
is what makes the mock usable in a test suite.

## Design consequences

* No dependency on a schema library means apikit's validator understands a documented
  subset and ignores the rest. This is stated in the README instead of pretended
  otherwise.
* The mock never executes anything from the spec: no templates, no eval, no shell.
  A malicious spec can produce nonsense JSON but cannot run code.
* Every renderer is pure: the CLI builds a string and writes it once, so text, JSON
  and Markdown output all come from the same in-memory result.
