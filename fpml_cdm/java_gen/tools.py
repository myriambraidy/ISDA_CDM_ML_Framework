"""Agent tools for CDM Java code generation.

Each tool is a plain function that returns a dict. The agent loop calls these
via LLM tool-use; they are also independently testable.
"""

from __future__ import annotations

import json
import platform
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Union

from .schema_index import SchemaIndex

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GENERATED_DIR = PROJECT_ROOT / "rosetta-validator" / "generated"
TEMPLATE_DIR = Path(__file__).resolve().parent / "java_templates"

_idx: Optional[SchemaIndex] = None

# Default generation target (used when no agent run has set an active target).
_LEGACY_JAVA_FILENAME = "CdmTradeBuilder.java"
_LEGACY_JAVA_CLASS = "CdmTradeBuilder"
_DEFAULT_JAVA_CLASS = "CdmTradeBuilder"
_DEFAULT_JAVA_FILENAME = "CdmTradeBuilder.java"

_JAVA_TARGET: Dict[str, str] = {
    "class_name": _DEFAULT_JAVA_CLASS,
    "filename": _DEFAULT_JAVA_FILENAME,
}


def json_stem_to_java_class_name(stem: str) -> str:
    """Build a valid Java public class name from a JSON filename stem (PascalCase)."""
    stem = stem.strip()
    if not stem:
        return "GeneratedCdmTrade"
    parts = re.split(r"[^a-zA-Z0-9]+", stem)
    parts = [p for p in parts if p]
    if not parts:
        return "GeneratedCdmTrade"
    words: List[str] = []
    for p in parts:
        p = re.sub(r"[^a-zA-Z0-9]", "", p)
        if not p:
            continue
        if p[0].isdigit():
            p = "N" + p
        words.append(p[0].upper() + p[1:].lower() if len(p) > 1 else p.upper())
    if not words:
        return "GeneratedCdmTrade"
    return "".join(words)


def set_java_generation_target(
    *,
    cdm_json_path: Optional[str] = None,
    class_name: Optional[str] = None,
) -> None:
    """Set the active output filename and class name for one Java generation run."""
    global _JAVA_TARGET
    if class_name is not None:
        cn = class_name.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", cn):
            raise ValueError(f"Invalid Java class name: {class_name!r}")
    elif cdm_json_path is not None:
        cn = json_stem_to_java_class_name(Path(cdm_json_path).stem)
    else:
        cn = _DEFAULT_JAVA_CLASS
    _JAVA_TARGET = {"class_name": cn, "filename": f"{cn}.java"}


def reset_java_generation_target() -> None:
    """Restore defaults (e.g. after an agent run)."""
    global _JAVA_TARGET
    _JAVA_TARGET = {"class_name": _DEFAULT_JAVA_CLASS, "filename": _DEFAULT_JAVA_FILENAME}


def get_active_java_class_name() -> str:
    return _JAVA_TARGET["class_name"]


def get_active_java_filename() -> str:
    return _JAVA_TARGET["filename"]


def _resolve_java_filename(filename: Optional[str]) -> str:
    """Resolve tool filename: None → active; legacy CdmTradeBuilder.java → active when overridden."""
    fn = get_active_java_filename() if filename is None else filename
    if fn == _LEGACY_JAVA_FILENAME and get_active_java_filename() != _LEGACY_JAVA_FILENAME:
        return get_active_java_filename()
    return fn


def _resolve_java_class_name(class_name: Optional[str]) -> str:
    """Resolve run_java class: None → active; legacy CdmTradeBuilder → active when overridden."""
    cn = get_active_java_class_name() if class_name is None else class_name
    if cn == _LEGACY_JAVA_CLASS and get_active_java_class_name() != _LEGACY_JAVA_CLASS:
        return get_active_java_class_name()
    return cn

# Known schema-to-Java type mismatches.
# The JSON schemas represent dates as strings, but CDM Java uses typed date classes.
WELL_KNOWN_IMPORTS: Dict[str, str] = {
    "MetaFields": "com.rosetta.model.metafields.MetaFields",
    "FieldWithMetaDate": "com.rosetta.model.metafields.FieldWithMetaDate",
    "FieldWithMetaString": "com.rosetta.model.metafields.FieldWithMetaString",
    "ReferenceWithMetaString": "com.rosetta.model.metafields.ReferenceWithMetaString",
    "Date": "com.rosetta.model.lib.records.Date",
    "Reference": "com.rosetta.model.lib.meta.Reference",
    "BigDecimal": "java.math.BigDecimal",
    "BusinessCenterEnum": "cdm.base.datetime.BusinessCenterEnum",
    "BusinessDayConventionEnum": "cdm.base.datetime.BusinessDayConventionEnum",
    "DayTypeEnum": "cdm.base.datetime.DayTypeEnum",
    "RollConventionEnum": "cdm.base.datetime.RollConventionEnum",
    "PeriodEnum": "cdm.base.datetime.PeriodEnum",
    "PeriodExtendedEnum": "cdm.base.datetime.PeriodExtendedEnum",
    "DayCountFractionEnum": "cdm.base.datetime.daycount.DayCountFractionEnum",
    "InterestRatePayout": "cdm.product.asset.InterestRatePayout",
    "RateSpecification": "cdm.product.asset.RateSpecification",
    "FloatingRateSpecification": "cdm.product.asset.FloatingRateSpecification",
    "FixedRateSpecification": "cdm.product.asset.FixedRateSpecification",
    "PriceTypeEnum": "cdm.observable.asset.PriceTypeEnum",
    "AssetClassEnum": "cdm.base.staticdata.asset.common.AssetClassEnum",
    "AssetIdTypeEnum": "cdm.base.staticdata.asset.common.AssetIdTypeEnum",
    "PartyIdentifierTypeEnum": "cdm.base.staticdata.party.PartyIdentifierTypeEnum",
    "FloatingRateIndexEnum": "cdm.base.staticdata.asset.rates.FloatingRateIndexEnum",
    "CounterpartyRoleEnum": "cdm.base.staticdata.party.CounterpartyRoleEnum",
}

_REFERENCE_SCHEMA = "com-rosetta-model-lib-meta-Reference.schema.json"

JAVA_TYPE_OVERRIDES: Dict[str, Dict[str, str]] = {
    "tradeDate": {
        "java_class": "com.rosetta.model.metafields.FieldWithMetaDate",
        "java_usage": "com.rosetta.model.metafields.FieldWithMetaDate.builder().setValue(com.rosetta.model.lib.records.Date.of(YYYY, MM, DD)).build()",
        "note": "Java CDM uses FieldWithMetaDate, not FieldWithMetaString",
    },
    "valueDate": {
        "java_class": "com.rosetta.model.lib.records.Date",
        "java_usage": "com.rosetta.model.lib.records.Date.of(YYYY, MM, DD)",
        "note": "Java CDM uses Rosetta Date, not String or LocalDate",
    },
    "adjustedDate": {
        "java_class": "com.rosetta.model.metafields.FieldWithMetaDate",
        "java_usage": "com.rosetta.model.metafields.FieldWithMetaDate.builder().setValue(com.rosetta.model.lib.records.Date.of(YYYY, MM, DD)).build()",
        "note": "Java CDM uses FieldWithMetaDate for adjusted dates",
    },
    "unadjustedDate": {
        "java_class": "com.rosetta.model.lib.records.Date",
        "java_usage": "com.rosetta.model.lib.records.Date.of(YYYY, MM, DD)",
        "note": "Java CDM uses Rosetta Date, not String",
    },
}


def _get_index() -> SchemaIndex:
    global _idx
    if _idx is None:
        _idx = SchemaIndex()
    return _idx


def _setter_hint_for_property(
    prop_name: str,
    *,
    is_array: bool,
    ref: Optional[str],
) -> tuple[str, Optional[str]]:
    if is_array:
        return (f"add{prop_name[0].upper()}{prop_name[1:]}", None)
    if prop_name == "address" and ref == _REFERENCE_SCHEMA:
        return (
            "setReference",
            "JSON property address maps to setReference(Reference); there is no setAddress.",
        )
    if prop_name == "globalReference":
        return ("setGlobalReference", None)
    if prop_name == "externalReference":
        return ("setExternalReference", None)
    return (f"set{prop_name[0].upper()}{prop_name[1:]}", None)


# ── Tool 1: inspect_cdm_json ─────────────────────────────────────────

def inspect_cdm_json(json_path: str) -> Dict[str, object]:
    """Analyze CDM JSON structure, resolve types via schema $refs."""
    idx = _get_index()
    raw = Path(json_path).read_text(encoding="utf-8")
    cdm_data = json.loads(raw)
    trade = cdm_data.get("trade", cdm_data)

    tree: List[Dict[str, object]] = []
    enums_used: List[Dict[str, str]] = []
    type_counts: Dict[str, int] = defaultdict(int)

    trade_schema_file = "cdm-event-common-Trade.schema.json"
    ref_patterns_sample: List[Dict[str, object]] = []
    reference_pattern_total: Dict[str, int] = {"n": 0}
    max_ref_samples = 40

    def _resolve_prop(parent_schema_file: str, prop_name: str) -> Optional[str]:
        """Get the $ref schema filename for a property of a parent type."""
        schema = idx.get_schema_by_ref(parent_schema_file)
        if schema is None:
            return None
        prop_def = schema.get("properties", {}).get(prop_name, {})
        ref = prop_def.get("$ref")
        if ref:
            return ref
        items = prop_def.get("items")
        if isinstance(items, dict):
            return items.get("$ref")
        return None

    def _is_array_prop(parent_schema_file: str, prop_name: str) -> bool:
        schema = idx.get_schema_by_ref(parent_schema_file)
        if schema is None:
            return False
        prop_def = schema.get("properties", {}).get(prop_name, {})
        return prop_def.get("type") == "array"

    def walk(
        node: object,
        path: str,
        schema_file: Optional[str],
    ) -> None:
        if isinstance(node, dict):
            present_keys = [
                k for k in ("globalReference", "externalReference") if k in node
            ]
            if present_keys:
                reference_pattern_total["n"] += 1
                if len(ref_patterns_sample) < max_ref_samples:
                    ref_patterns_sample.append(
                        {"json_path": path, "keys": present_keys}
                    )
            for key, child in node.items():
                child_path = f"{path}.{key}"
                child_ref: Optional[str] = None
                if schema_file:
                    child_ref = _resolve_prop(schema_file, key)

                if isinstance(child, dict):
                    if child_ref:
                        title = idx.file_to_type_name(child_ref)
                        java_class = idx.schema_ref_to_java_class(child_ref)
                        if title:
                            type_counts[title] += 1
                        tree.append({
                            "json_path": child_path,
                            "cdm_type": title or "object",
                            "schema_ref": child_ref,
                            "java_class": java_class or "",
                            "is_array": False,
                            "is_leaf": False,
                        })
                        walk(child, child_path, child_ref)
                    else:
                        tree.append({
                            "json_path": child_path,
                            "cdm_type": "object",
                            "schema_ref": None,
                            "java_class": "",
                            "is_array": False,
                            "is_leaf": False,
                        })
                        walk(child, child_path, None)

                elif isinstance(child, list):
                    is_arr = True
                    item_ref = child_ref
                    item_title = idx.file_to_type_name(item_ref) if item_ref else None
                    if item_title:
                        type_counts[item_title] += len(child)
                    tree.append({
                        "json_path": child_path,
                        "cdm_type": f"{item_title}[]" if item_title else "array",
                        "schema_ref": item_ref,
                        "java_class": idx.schema_ref_to_java_class(item_ref) if item_ref else "",
                        "is_array": True,
                        "array_length": len(child),
                        "is_leaf": False,
                    })
                    for i, item in enumerate(child):
                        item_path = f"{child_path}[{i}]"
                        if isinstance(item, dict):
                            walk(item, item_path, item_ref)
                        else:
                            tree.append({
                                "json_path": item_path,
                                "cdm_type": type(item).__name__,
                                "value": item,
                                "is_leaf": True,
                            })

                else:
                    # leaf value — check if it's an enum
                    leaf_type = type(child).__name__
                    if child_ref and idx.is_enum(child_ref):
                        enum_title = idx.file_to_type_name(child_ref) or child_ref
                        enums_used.append({
                            "path": child_path,
                            "enum_type": enum_title,
                            "value": str(child),
                        })
                        leaf_type = enum_title
                        type_counts[enum_title] += 1

                    tree.append({
                        "json_path": child_path,
                        "cdm_type": leaf_type,
                        "value": child,
                        "is_leaf": True,
                    })

    type_counts["Trade"] = 1
    walk(trade, "$.trade", trade_schema_file)

    # Build type_registry: resolved Java info for every unique schema_ref
    unique_refs = {n["schema_ref"] for n in tree if n.get("schema_ref")}
    unique_refs.add(trade_schema_file)
    # Also include enum schemas referenced by enums_used
    for e in enums_used:
        enum_file = idx.type_name_to_file(e["enum_type"])
        if enum_file:
            unique_refs.add(enum_file)
    type_registry: Dict[str, Dict[str, object]] = {}
    for ref in sorted(unique_refs):
        fq, pkg, simple = idx.java_class_parts(ref)
        if not fq:
            continue
        type_registry[ref] = {
            "java_class": fq,
            "java_package": pkg,
            "simple_name": simple,
            "import_statement": f"import {fq};",
            "builder_entry": f"{simple}.builder()",
            "is_enum": idx.is_enum(ref),
        }

    # Annotate tree nodes that have known Java type overrides
    java_type_warnings: List[Dict[str, str]] = []
    for node in tree:
        prop_name = node["json_path"].rsplit(".", 1)[-1] if "." in str(node.get("json_path", "")) else ""
        prop_name = prop_name.split("[")[0]
        if prop_name in JAVA_TYPE_OVERRIDES:
            override = JAVA_TYPE_OVERRIDES[prop_name]
            java_type_warnings.append({
                "json_path": str(node["json_path"]),
                "property": prop_name,
                "schema_type": str(node.get("cdm_type", "")),
                "actual_java_class": override["java_class"],
                "java_usage": override["java_usage"],
                "note": override["note"],
            })

    ref_note = (
        "For ReferenceWithMeta* builders use setGlobalReference(String), "
        "setExternalReference(String), and setReference(Reference) for JSON property address. "
        "Do not use setAddress()."
    )
    return {
        "root_type": "Trade",
        "total_nodes": len(tree),
        "tree": tree,
        "enums_used": enums_used,
        "type_summary": dict(type_counts),
        "type_registry": type_registry,
        "java_type_warnings": java_type_warnings,
        "well_known_imports": WELL_KNOWN_IMPORTS,
        "well_known_imports_note": (
            "Consider imports for these symbols when building trades; not all appear as explicit "
            "values in the JSON instance."
        ),
        "reference_pattern_total": reference_pattern_total["n"],
        "reference_patterns_sample": ref_patterns_sample,
        "reference_api_note": ref_note if reference_pattern_total["n"] else None,
    }


# ── Tool 2: lookup_cdm_schema ────────────────────────────────────────

def lookup_cdm_schema(type_name: str) -> Dict[str, object]:
    """Look up the full schema definition for a CDM type."""
    idx = _get_index()

    schema_file = idx.type_name_to_file(type_name)
    if schema_file is None:
        return {"error": f"Unknown CDM type: {type_name}"}

    schema = idx.get_schema_by_ref(schema_file)
    if schema is None:
        return {"error": f"Could not load schema: {schema_file}"}

    fq, pkg, simple = idx.java_class_parts(schema_file)
    required_set = set(schema.get("required", []))

    properties: Dict[str, Dict[str, object]] = {}
    for prop_name, prop_def in schema.get("properties", {}).items():
        is_array = prop_def.get("type") == "array"
        ref = prop_def.get("$ref")
        if not ref and is_array:
            items = prop_def.get("items")
            if isinstance(items, dict):
                ref = items.get("$ref")

        prop_java = idx.schema_ref_to_java_class(ref) if ref else None

        setter, setter_note = _setter_hint_for_property(
            prop_name, is_array=is_array, ref=ref
        )

        entry: Dict[str, object] = {
            "type": prop_def.get("type", "object"),
            "ref": ref,
            "java_class": prop_java,
            "required": prop_name in required_set,
            "is_array": is_array,
            "setter_hint": setter,
            "description": prop_def.get("description", ""),
        }
        if setter_note is not None:
            entry["setter_note"] = setter_note
        properties[prop_name] = entry

    resolved_title = str(schema.get("title", type_name))
    result: Dict[str, object] = {
        "type_name": resolved_title,
        "schema_file": schema_file,
        "java_class": fq,
        "java_package": pkg,
        "description": schema.get("description", ""),
        "properties": properties,
        "required_fields": sorted(required_set),
    }
    if "ReferenceWithMeta" in resolved_title or "ReferenceWithMeta" in type_name:
        result["builder_reference_note"] = (
            "ReferenceWithMeta* builders: use setGlobalReference, setExternalReference, "
            "or setReference(Reference) for JSON address. There is no setAddress()."
        )
    return result


# ── Tool 3: resolve_java_type ────────────────────────────────────────

def resolve_java_type(schema_ref: str) -> Dict[str, str]:
    """Resolve a schema $ref to fully-qualified Java class info."""
    idx = _get_index()
    fq, pkg, simple = idx.java_class_parts(schema_ref)

    if not fq:
        return {"error": f"Cannot resolve schema ref: {schema_ref}"}

    return {
        "java_class": fq,
        "java_package": pkg,
        "simple_name": simple,
        "builder_class": f"{simple}.{simple}Builder",
        "import_statement": f"import {fq};",
        "builder_entry": f"{simple}.builder()",
    }


# ── Tool 4: list_enum_values ─────────────────────────────────────────

def list_enum_values(enum_name: str) -> Dict[str, object]:
    """List all valid values for a CDM enum type."""
    idx = _get_index()

    schema_file = idx.type_name_to_file(enum_name)
    if schema_file is None:
        return {"error": f"Unknown enum type: {enum_name}"}

    if not idx.is_enum(schema_file):
        return {"error": f"{enum_name} is not an enum type"}

    fq, pkg, simple = idx.java_class_parts(schema_file)
    raw_values = idx.enum_values(schema_file)

    values: List[Dict[str, str]] = []
    for val in raw_values:
        java_ident = idx.enum_json_value_java_identifier(schema_file, val)
        values.append({
            "json_value": val,
            "java_constant": f"{simple}.{java_ident}",
        })

    has_special = any(re.search(r"[^a-zA-Z0-9]", v["json_value"]) for v in values)
    out: Dict[str, object] = {
        "enum_name": enum_name,
        "java_class": fq,
        "java_package": pkg,
        "import_statement": f"import {fq};",
        "values": values,
    }
    if has_special:
        out["enum_constant_warning"] = (
            "Some JSON enum values contain punctuation; constants use schema oneOf titles or "
            "sanitized names. If compilation fails, verify against the shaded CDM JAR."
        )
    return out


# ── Tool 5: get_java_template ────────────────────────────────────────

_JAVA_TEMPLATE_BODY = """\
// === IMPORTS_PLACEHOLDER ===

public class __CLASS_NAME__ {

    public static cdm.event.common.Trade buildTrade() {
        // === BUILDER_CODE_PLACEHOLDER ===
    }

    public static void main(String[] args) throws Exception {
        cdm.event.common.Trade trade = buildTrade();

        com.fasterxml.jackson.databind.ObjectMapper mapper =
            com.regnosys.rosetta.common.serialisation.RosettaObjectMapper
                .getNewRosettaObjectMapper();

        String tradeJson = mapper.writerWithDefaultPrettyPrinter()
            .writeValueAsString(trade);

        // Wrap in {"trade": ...} to match input format
        System.out.println("{\\"trade\\":" + tradeJson + "}");
    }
}
"""


def _java_template_source_for_class(class_name: str) -> str:
    return _JAVA_TEMPLATE_BODY.replace("__CLASS_NAME__", class_name)


def get_java_template() -> Dict[str, object]:
    """Return the Java file boilerplate template for the active generation class name."""
    cn = get_active_java_class_name()
    return {
        "template": _java_template_source_for_class(cn),
        "class_name": cn,
        "placeholders": [
            "// === IMPORTS_PLACEHOLDER ===",
            "// === BUILDER_CODE_PLACEHOLDER ===",
        ],
    }


# ── Tool 6: write_java_file ──────────────────────────────────────────

def write_java_file(
    code: str,
    filename: Optional[str] = None,
) -> Dict[str, object]:
    """Write complete Java source code to rosetta-validator/generated/."""
    fn = _resolve_java_filename(filename)
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    path = GENERATED_DIR / fn
    path.write_text(code, encoding="utf-8")
    return {
        "success": True,
        "path": str(path),
        "lines": code.count("\n") + 1,
    }


# ── Tool 7: read_java_file ───────────────────────────────────────────

def read_java_file(
    filename: Optional[str] = None,
) -> Dict[str, object]:
    """Read the current content of a generated Java source file."""
    fn = _resolve_java_filename(filename)
    path = GENERATED_DIR / fn
    if not path.exists():
        return {"error": f"File not found: {path}"}
    content = path.read_text(encoding="utf-8")
    return {
        "path": str(path),
        "content": content,
        "lines": content.count("\n") + 1,
    }


# ── Tool 8: patch_java_file ──────────────────────────────────────────


def _normalize_whitespace_for_match(s: str) -> str:
    """Normalize for fuzzy match: strip trailing whitespace per line, rejoin with \\n."""
    return "\n".join(line.rstrip() for line in s.split("\n"))


def _build_normalized_to_original_map(content: str) -> tuple[str, List[int]]:
    """Return (normalized_content, list of original index per normalized char)."""
    lines = content.split("\n")
    norm_parts: List[str] = [line.rstrip() for line in lines]
    normalized_content = "\n".join(norm_parts)
    norm_to_orig: List[int] = []
    orig_i = 0
    for i, line in enumerate(lines):
        stripped = norm_parts[i]
        for _ in stripped:
            norm_to_orig.append(orig_i)
            orig_i += 1
        if i < len(lines) - 1:
            norm_to_orig.append(orig_i)
            orig_i += 1  # \n
    return normalized_content, norm_to_orig


def _suggest_old_text_from_file(content: str, old: str) -> Optional[str]:
    """If old has a distinctive token, return the exact line(s) from content containing it."""
    stripped = old.strip()
    if not stripped:
        return None
    # Prefer a short distinctive substring (e.g. method call)
    for token in (".setIdentifierType", ".setValue(", ".build()", ".addParty("):
        if token in stripped:
            key = token
            break
    else:
        key = stripped[:40].strip() if len(stripped) > 40 else stripped
    lines = content.split("\n")
    for line in lines:
        if key in line:
            return line
    return None


def patch_java_file(
    old_text: str = "",
    new_text: str = "",
    filename: Optional[str] = None,
    patches: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, object]:
    """Replace text sections in a generated Java file.

    Supports single replacement (old_text/new_text) or batch mode via
    the ``patches`` parameter (list of {"old_text": ..., "new_text": ...}).
    Uses exact match first; if not found, tries normalized whitespace match (strip
    trailing per line). On failure, may return suggested_old_text from the file.
    """
    fn = _resolve_java_filename(filename)
    path = GENERATED_DIR / fn
    if not path.exists():
        return {"error": f"File not found: {path}"}

    # Normalise into a list of (old, new) pairs
    pairs: List[tuple[str, str]] = []
    if patches:
        for p in patches:
            pairs.append((p["old_text"], p["new_text"]))
    elif old_text:
        pairs.append((old_text, new_text))
    else:
        return {"error": "Provide old_text/new_text or patches list"}

    content = path.read_text(encoding="utf-8")
    total_replacements = 0
    errors: List[str] = []
    matched_normalized = False
    suggested_for_not_found: List[Optional[str]] = []

    for old, new in pairs:
        normalized_content, norm_to_orig = _build_normalized_to_original_map(content)
        if old == new:
            errors.append(f"No-op: old_text == new_text ({old[:80]!r})")
            suggested_for_not_found.append(None)
            continue
        count = content.count(old)
        if count > 0:
            content = content.replace(old, new)
            total_replacements += count
            suggested_for_not_found.append(None)
            continue
        # Exact not found: try normalized match
        norm_old = _normalize_whitespace_for_match(old)
        norm_count = normalized_content.count(norm_old)
        if norm_count == 1:
            start = normalized_content.find(norm_old)
            end = start + len(norm_old)
            orig_start = norm_to_orig[start]
            orig_end = norm_to_orig[end] if end < len(norm_to_orig) else len(content)
            content = content[:orig_start] + new + content[orig_end:]
            total_replacements += 1
            matched_normalized = True
            suggested_for_not_found.append(None)
            continue
        if norm_count > 1:
            errors.append(f"Not found (ambiguous normalized match): {old[:80]!r}")
        else:
            errors.append(f"Not found: {old[:120]!r}")
        suggested_for_not_found.append(_suggest_old_text_from_file(content, old))

    path.write_text(content, encoding="utf-8")

    result: Dict[str, object] = {
        "success": total_replacements > 0,
        "replacements_made": total_replacements,
        "path": str(path),
    }
    if matched_normalized:
        result["matched_with_normalized_whitespace"] = True
    if errors:
        result["warnings"] = errors
        suggested = [s for s in suggested_for_not_found if s is not None]
        if suggested:
            result["suggested_old_text"] = suggested[0] if len(suggested) == 1 else suggested
    return result


# ── Tool 9: compile_java ─────────────────────────────────────────────

JAR_PATH = PROJECT_ROOT / "rosetta-validator" / "target" / "rosetta-validator-1.0.0.jar"


def _parse_javac_errors(stderr: str, src_path: Path) -> List[Dict[str, object]]:
    """Parse javac stderr into structured error dicts."""
    errors: List[Dict[str, object]] = []
    lines = stderr.splitlines()
    src_name = src_path.name

    i = 0
    while i < len(lines):
        line = lines[i]
        # javac error format: filename:line: error: message
        match = re.match(r".*?(\d+):\s*error:\s*(.*)", line)
        if match:
            line_no = int(match.group(1))
            message = match.group(2)

            # Collect continuation lines (indented or caret lines)
            i += 1
            while i < len(lines) and not re.match(r".*?\d+:\s*error:", lines[i]) and not lines[i].startswith("Note:") and lines[i] != "":
                continuation = lines[i]
                if continuation.strip() == "^":
                    i += 1
                    continue
                message += "\n" + continuation
                i += 1

            source_line = ""
            try:
                src_lines = src_path.read_text(encoding="utf-8").splitlines()
                if 0 < line_no <= len(src_lines):
                    source_line = src_lines[line_no - 1]
            except OSError:
                pass

            error_entry: Dict[str, object] = {
                "line": line_no,
                "message": message.strip(),
                "source_line": source_line,
            }

            # Add structured hints for common error patterns
            type_mismatch = re.search(
                r"incompatible types:\s+(\S+)\s+cannot be converted to\s+(\S+)",
                message,
            )
            if type_mismatch:
                got, expected = type_mismatch.group(1), type_mismatch.group(2)
                error_entry["hint"] = (
                    f"Change type from {got} to {expected}. "
                    f"Use {expected}.builder() if it is a builder type, "
                    f"or add 'import' for {expected}."
                )
                error_entry["expected_type"] = expected
                error_entry["actual_type"] = got

            symbol_match = re.search(
                r"cannot find symbol.*?symbol:\s+(?:variable|class)\s+(\w+)",
                message,
                re.DOTALL,
            )
            if symbol_match and not type_mismatch:
                missing = symbol_match.group(1)
                error_entry["hint"] = (
                    f"'{missing}' is not imported. Add the import statement "
                    f"or use fully-qualified name (e.g. package.{missing})."
                )
                error_entry["missing_symbol"] = missing

            errors.append(error_entry)
        else:
            i += 1

    return errors


def compile_java(
    filename: Optional[str] = None,
) -> Dict[str, object]:
    """Compile a Java file against the CDM classpath."""
    fn = _resolve_java_filename(filename)
    src_path = GENERATED_DIR / fn
    if not src_path.exists():
        return {"success": False, "errors": [{"message": f"Source file not found: {src_path}"}], "error_count": 1}

    if not JAR_PATH.exists():
        return {
            "success": False,
            "errors": [{"message": "rosetta-validator JAR not found. Run: make rosetta-build"}],
            "error_count": 1,
        }

    sep = ";" if platform.system() == "Windows" else ":"
    classpath = f"{JAR_PATH}{sep}{GENERATED_DIR}"

    try:
        result = subprocess.run(
            ["javac", "-cp", classpath, "-d", str(GENERATED_DIR), str(src_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        return {"success": False, "errors": [{"message": "javac not found on PATH"}], "error_count": 1}
    except subprocess.TimeoutExpired:
        return {"success": False, "errors": [{"message": "Compilation timed out (60s)"}], "error_count": 1}

    if result.returncode == 0:
        class_file = str(GENERATED_DIR / fn.replace(".java", ".class"))
        return {"success": True, "class_file": class_file, "warnings": []}

    errors = _parse_javac_errors(result.stderr, src_path)
    return {
        "success": False,
        "errors": errors,
        "error_count": len(errors),
        "raw_stderr": result.stderr[:2000],
    }


# ── Tool 10: run_java ────────────────────────────────────────────────

def run_java(
    class_name: Optional[str] = None,
    timeout: int = 30,
) -> Dict[str, object]:
    """Execute a compiled Java class and capture output."""
    cn = _resolve_java_class_name(class_name)
    if not JAR_PATH.exists():
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": "rosetta-validator JAR not found. Run: make rosetta-build",
            "stdout_is_valid_json": False,
        }

    sep = ";" if platform.system() == "Windows" else ":"
    classpath = f"{JAR_PATH}{sep}{GENERATED_DIR}"

    try:
        result = subprocess.run(
            ["java", "-cp", classpath, cn],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {
            "success": False, "exit_code": -1,
            "stdout": "", "stderr": "java not found on PATH",
            "stdout_is_valid_json": False,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False, "exit_code": -1,
            "stdout": "", "stderr": f"Execution timed out ({timeout}s)",
            "stdout_is_valid_json": False,
        }

    is_json = False
    if result.stdout.strip():
        try:
            json.loads(result.stdout)
            is_json = True
        except (json.JSONDecodeError, ValueError):
            pass

    return {
        "success": result.returncode == 0,
        "exit_code": result.returncode,
        "stdout": result.stdout[:10000],
        "stdout_is_valid_json": is_json,
        "stderr": result.stderr[:3000],
    }


# ── Tool 11: diff_json ───────────────────────────────────────────────

def _count_leaves(node: object) -> int:
    """Count leaf values in a JSON-like structure."""
    if isinstance(node, dict):
        return sum(_count_leaves(v) for v in node.values())
    if isinstance(node, list):
        return sum(_count_leaves(v) for v in node)
    return 1


def _values_equal(a: object, b: object, tol: float = 1e-10) -> bool:
    """Compare two leaf values with type awareness and float tolerance."""
    if type(a) is type(b):
        if isinstance(a, float):
            return abs(a - b) < tol  # type: ignore[operator]
        return a == b
    # int vs float: 5 == 5.0
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < tol  # type: ignore[arg-type]
    return False


def diff_json(
    expected_json_path: str,
    actual_json: str,
) -> Dict[str, object]:
    """Deep-compare expected CDM JSON with Java-produced output."""
    expected = json.loads(Path(expected_json_path).read_text(encoding="utf-8"))
    actual = json.loads(actual_json)

    diffs: List[Dict[str, object]] = []
    extra: List[str] = []
    total = 0
    matched = 0

    def compare(exp: object, act: object, path: str) -> None:
        nonlocal total, matched

        if isinstance(exp, dict) and isinstance(act, dict):
            all_keys = set(exp.keys()) | set(act.keys())
            for key in sorted(all_keys):
                child_path = f"{path}.{key}"
                if key not in act:
                    leaf_count = _count_leaves(exp[key])
                    total += leaf_count
                    diffs.append({
                        "path": child_path,
                        "expected": exp[key],
                        "actual": None,
                        "type": "missing_in_actual",
                    })
                elif key not in exp:
                    extra.append(child_path)
                else:
                    compare(exp[key], act[key], child_path)

        elif isinstance(exp, list) and isinstance(act, list):
            for i in range(max(len(exp), len(act))):
                child_path = f"{path}[{i}]"
                if i >= len(act):
                    leaf_count = _count_leaves(exp[i])
                    total += leaf_count
                    diffs.append({
                        "path": child_path,
                        "expected": exp[i],
                        "actual": None,
                        "type": "missing_in_actual",
                    })
                elif i >= len(exp):
                    extra.append(child_path)
                else:
                    compare(exp[i], act[i], child_path)

        else:
            total += 1
            if _values_equal(exp, act):
                matched += 1
            else:
                diff_type = "type_mismatch" if type(exp) is not type(act) else "value_mismatch"
                diffs.append({
                    "path": path,
                    "expected": exp,
                    "actual": act,
                    "type": diff_type,
                })

    compare(expected, actual, "$")

    pct = (matched / total * 100) if total > 0 else 100.0
    return {
        "match": len(diffs) == 0,
        "match_percentage": round(pct, 1),
        "total_leaf_values": total,
        "matched_leaf_values": matched,
        "differences": diffs[:50],
        "extra_in_actual": extra[:20],
    }


# ── Tool 12: validate_output ─────────────────────────────────────────

def validate_output(json_string: str) -> Dict[str, object]:
    """Validate JSON against the official CDM Trade schema."""
    from fpml_cdm.validator import validate_cdm_official_schema

    data = json.loads(json_string)
    trade_dict = data.get("trade", data)

    issues = validate_cdm_official_schema(trade_dict)
    error_dicts = [issue.to_dict() for issue in issues]
    return {
        "valid": len(issues) == 0,
        "errors": error_dicts,
        "error_count": len(issues),
    }


# ── Tool 13: finish ──────────────────────────────────────────────────

def finish(
    status: str,
    summary: str,
    java_file: str = "",
    match_percentage: float = 0.0,
) -> Dict[str, object]:
    """Signal agent loop completion."""
    return {
        "status": status,
        "summary": summary,
        "java_file": java_file,
        "match_percentage": match_percentage,
    }
