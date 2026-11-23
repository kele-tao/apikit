"""Deterministic, schema-driven example generation.

The generator is seeded: the same spec and the same seed always produce the
same bytes, which is what makes the mock server usable as a test double.
"""

from __future__ import annotations

import base64
import random
import string
import uuid
from typing import Any

from .pattern import PatternTooComplex
from .pattern import sample as sample_pattern


class GenerationError(ValueError):
    """The schema cannot be turned into an example."""


FIRST_NAMES = ("Ada", "Grace", "Alan", "Linus", "Margaret", "Dennis", "Barbara", "Ken")
LAST_NAMES = ("Lovelace", "Hopper", "Turing", "Torvalds", "Hamilton", "Ritchie", "Liskov")
CITIES = ("Berlin", "Lisbon", "Osaka", "Toronto", "Nairobi", "Austin", "Wellington", "Tallinn")
COMPANIES = ("Northwind", "Globex", "Initech", "Umbra", "Hyperion", "Acme")
WORDS = (
    "amber", "birch", "cobalt", "delta", "ember", "falcon", "garnet", "harbor",
    "indigo", "juniper", "kelp", "lumen", "marble", "nimbus", "onyx", "prism",
)
PET_NAMES = ("Bella", "Charlie", "Luna", "Max", "Coco", "Rocky", "Daisy", "Milo")
PET_TAGS = ("friendly", "vaccinated", "trained", "senior", "playful", "calm")
STATUS_WORDS = ("available", "pending", "sold")


def _rng_for(seed: int, name: str) -> random.Random:
    """A per-node RNG so generation order does not change the values."""
    return random.Random(f"{seed}:{name}")


def generate_example(
    schema: Any, seed: int = 0, name: str = "root", _depth: int = 0
) -> Any:
    """Generate one deterministic example value for *schema*."""
    if _depth > 12:
        raise GenerationError(f"schema at {name} is nested deeper than 12 levels")
    if not isinstance(schema, dict):
        return None
    rng = _rng_for(seed, name)

    if "const" in schema:
        return schema["const"]
    if isinstance(schema.get("example"), (str, int, float, bool)):
        return schema["example"]
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        return rng.choice(schema["enum"])

    for keyword in ("allOf",):
        parts = schema.get(keyword)
        if isinstance(parts, list) and parts:
            merged: dict[str, Any] = {}
            for index, part in enumerate(parts):
                value = generate_example(part, seed, f"{name}/{keyword}/{index}", _depth + 1)
                if isinstance(value, dict):
                    merged.update(value)
                elif value is not None:
                    return value
            return merged

    for keyword in ("oneOf", "anyOf"):
        parts = schema.get(keyword)
        if isinstance(parts, list) and parts:
            return generate_example(parts[0], seed, f"{name}/{keyword}/0", _depth + 1)

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((item for item in schema_type if item != "null"), "null")
    if schema_type is None:
        if "properties" in schema or "additionalProperties" in schema:
            schema_type = "object"
        elif "items" in schema:
            schema_type = "array"
        else:
            schema_type = "string"

    if schema_type == "object":
        return _object(schema, seed, name, _depth)
    if schema_type == "array":
        return _array(schema, seed, name, _depth)
    if schema_type == "integer":
        return _number(schema, rng, integer=True)
    if schema_type == "number":
        return _number(schema, rng, integer=False)
    if schema_type == "boolean":
        return rng.random() < 0.5
    if schema_type == "null":
        return None
    return _string(schema, rng, name)


def _object(schema: dict[str, Any], seed: int, name: str, depth: int) -> dict[str, Any]:
    properties = schema.get("properties")
    result: dict[str, Any] = {}
    if isinstance(properties, dict):
        for key, child in properties.items():
            if isinstance(child, dict) and child.get("readOnly") and depth > 0:
                continue
            result[key] = generate_example(child, seed, f"{name}/properties/{key}", depth + 1)
    elif isinstance(schema.get("additionalProperties"), dict):
        rng = _rng_for(seed, name + "/additional")
        for index in range(2):
            key = f"field{index + 1}"
            child_name = f"{name}/additionalProperties/{index}"
            result[key] = generate_example(
                schema["additionalProperties"], seed, child_name, depth + 1
            )
    if not result:
        rng = _rng_for(seed, name + "/empty")
        return {"id": rng.randint(1, 9999)}
    return result


def _array(schema: dict[str, Any], seed: int, name: str, depth: int) -> list[Any]:
    minimum = int(schema.get("minItems") or 0)
    maximum = schema.get("maxItems")
    count = max(minimum, 2)
    if isinstance(maximum, int):
        count = min(count, maximum)
    count = min(count, 5)
    item_schema = schema.get("items") or {"type": "string"}
    return [
        generate_example(item_schema, seed + index, f"{name}/items/{index}", depth + 1)
        for index in range(count)
    ]


def _number(schema: dict[str, Any], rng: random.Random, integer: bool) -> Any:
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    low = float(minimum) if isinstance(minimum, (int, float)) else 1
    high = float(maximum) if isinstance(maximum, (int, float)) else low + 1000
    if high < low:
        low, high = high, low
    if integer:
        return int(rng.randint(int(round(low)), int(round(high))))
    value = round(rng.uniform(low, high), 2)
    multiple = schema.get("multipleOf")
    if isinstance(multiple, (int, float)) and multiple:
        value = round(round(value / multiple) * multiple, 6)
    return value


def _string(schema: dict[str, Any], rng: random.Random, name: str) -> str:
    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        try:
            return sample_pattern(pattern, _rng_for(int(rng.random() * 1_000_000), name))
        except PatternTooComplex:
            pass
    fmt = schema.get("format")
    value = _formatted(str(fmt) if fmt else "", rng)
    min_length = int(schema.get("minLength") or 0)
    max_length = schema.get("maxLength")
    if len(value) < min_length:
        value += "".join(rng.choice(string.ascii_lowercase) for _ in range(min_length - len(value)))
    if isinstance(max_length, int) and len(value) > max_length:
        value = value[:max_length] if max_length > 0 else "x"
    return value


def _formatted(fmt: str, rng: random.Random) -> str:
    if fmt in ("date",):
        return f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
    if fmt in ("date-time", "datetime"):
        return (
            f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
            f"T{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}Z"
        )
    if fmt == "time":
        return f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}Z"
    if fmt == "uuid":
        value = uuid.UUID(int=rng.getrandbits(128), version=4)
        return str(value)
    if fmt == "email":
        return f"{rng.choice(FIRST_NAMES).lower()}.{rng.choice(LAST_NAMES).lower()}@example.com"
    if fmt in ("uri", "url"):
        return f"https://api.example.com/{rng.choice(WORDS)}/{rng.randint(1, 999)}"
    if fmt == "hostname":
        return f"{rng.choice(WORDS)}.example.com"
    if fmt == "ipv4":
        return ".".join(str(rng.randint(1, 254)) for _ in range(4))
    if fmt == "ipv6":
        return ":".join(f"{rng.randint(1, 0xFFFF):x}" for _ in range(8))
    if fmt in ("byte", "binary"):
        payload = f"{rng.choice(WORDS)}-{rng.randint(100, 999)}".encode()
        return base64.b64encode(payload).decode()
    if fmt == "password":
        return "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(12))
    return f"{rng.choice(WORDS)}-{rng.randint(100, 999)}"
