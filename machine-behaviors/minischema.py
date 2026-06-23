#!/usr/bin/env python3
"""Dependency-free JSON-Schema (subset) validator.

The RealityEngine_Machines repo ships real JSON Schemas for the agent contract
(agent-binding, autonomy-policy, localai-writeback).  We validate the
externally-derived bindings against those canonical schemas so the prototype can
never drift from the corpus contract.  jsonschema is not installed in this
environment, so this implements the subset the agent schemas actually use:

    type, required, enum, const, properties, additionalProperties,
    items, minItems, minLength, minimum, maximum, uniqueItems, $ref, allOf,
    if / then.

$ref is resolved relative to the schema file's directory (local file refs only).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


class SchemaError(Exception):
    pass


def load_schema(path: Path) -> dict[str, Any]:
    with path.open() as handle:
        schema = json.load(handle)
    schema["__dir__"] = str(path.parent)
    return schema


def validate(instance: Any, schema: dict[str, Any], path: str = "$",
             root: dict[str, Any] | None = None, base_dir: str | None = None) -> list[str]:
    """Return a list of human-readable error strings (empty == valid)."""
    root = root if root is not None else schema
    base_dir = base_dir or schema.get("__dir__", ".")
    errors: list[str] = []

    if "$ref" in schema:
        ref = schema["$ref"]
        if ref.startswith("#/"):
            # intra-document ref: keep the SAME document root so nested refs resolve
            node: Any = root
            for part in ref[2:].split("/"):
                node = node[part]
            return validate(instance, node, path, root, base_dir)
        # local file ref (optionally with a #/fragment): switch root to that file
        file_part, _, fragment = ref.partition("#")
        file_path = Path(base_dir) / file_part
        target = json.loads(file_path.read_text())
        target["__dir__"] = str(file_path.parent)
        node = target
        if fragment:
            for part in fragment.strip("/").split("/"):
                node = node[part]
        return validate(instance, node, path, target, target["__dir__"])

    expected = schema.get("type")
    if expected:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_type_ok(instance, t) for t in types):
            errors.append(f"{path}: expected type {expected}, got {type(instance).__name__}")
            return errors

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in enum {schema['enum']}")
    if "minLength" in schema and isinstance(instance, str) and len(instance) < schema["minLength"]:
        errors.append(f"{path}: shorter than minLength {schema['minLength']}")
    if "minimum" in schema and isinstance(instance, (int, float)) and instance < schema["minimum"]:
        errors.append(f"{path}: below minimum {schema['minimum']}")
    if "maximum" in schema and isinstance(instance, (int, float)) and instance > schema["maximum"]:
        errors.append(f"{path}: above maximum {schema['maximum']}")

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required property '{key}'")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in props:
                    errors.append(f"{path}: unexpected property '{key}'")
        for key, value in instance.items():
            if key in props:
                errors += validate(value, props[key], f"{path}.{key}", root, base_dir)

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems {schema['minItems']}")
        if schema.get("uniqueItems") and len(instance) != len({json.dumps(x, sort_keys=True) for x in instance}):
            errors.append(f"{path}: items not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for idx, item in enumerate(instance):
                errors += validate(item, item_schema, f"{path}[{idx}]", root, base_dir)

    for sub in schema.get("allOf", []):
        if "if" in sub:
            cond_errors = validate(instance, sub["if"], path, root, base_dir)
            if not cond_errors and "then" in sub:
                errors += validate(instance, sub["then"], path, root, base_dir)
        else:
            errors += validate(instance, sub, path, root, base_dir)

    return errors


def _type_ok(instance: Any, expected: str) -> bool:
    py = _TYPES[expected]
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    return isinstance(instance, py)
