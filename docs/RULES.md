# Rule reference

Every finding apikit reports carries one of the rule codes below. The table is
generated from `src/apikit/diff.py` by `python benchmarks/gen_rules.py`, so it
cannot drift away from the implementation.

| Code | Severity | Meaning |
| --- | --- | --- |
| AP001 | breaking | Path removed |
| AP002 | info | Path added |
| AP003 | breaking | Operation removed |
| AP004 | info | Operation added |
| AP005 | breaking | Response status code removed |
| AP006 | info | Response status code added |
| AP007 | breaking | Request body became required |
| AP008 | info | Request body became optional |
| AP009 | breaking | Request body removed |
| AP010 | breaking | New required property in a request |
| AP011 | breaking | Property removed from a response |
| AP012 | warning | Property removed from a request |
| AP013 | breaking | Property renamed |
| AP014 | breaking | Property type changed |
| AP015 | info | Numeric type widened (integer to number) |
| AP016 | breaking | Enum value removed |
| AP017 | warning | Enum value added |
| AP018 | breaking | Optional property became required |
| AP019 | info | Required property became optional |
| AP020 | breaking | New required parameter |
| AP021 | warning | Parameter removed |
| AP022 | breaking | Parameter became required |
| AP023 | info | Parameter became optional |
| AP024 | breaking | Security scheme removed |
| AP025 | breaking | Security requirement tightened |
| AP026 | breaking | Constraint narrowed |
| AP027 | info | Constraint loosened |
| AP028 | warning | Default value changed |
| AP029 | warning | Format changed |
| AP030 | breaking | additionalProperties restricted |
| AP031 | breaking | Schema variant removed (oneOf/anyOf) |
| AP032 | warning | Discriminator mapping changed |
| AP033 | warning | Operation id renamed |
| AP034 | warning | Operation deprecated |
| AP035 | info | Security requirement removed |
| AP036 | breaking | Parameter type changed |
| AP037 | info | Property added |
| AP038 | info | Response property became required |
| AP039 | breaking | Response body removed |
| AP040 | breaking | Media type removed |
| AP041 | info | Schema variant added (oneOf/anyOf) |

Total: **41 rules** (21 breaking, 8 warning, 12 info).
