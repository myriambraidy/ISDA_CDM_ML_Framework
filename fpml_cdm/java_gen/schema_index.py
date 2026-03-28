"""CDM JSON Schema index: maps type names to schema files, resolves Java classes, detects enums."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SCHEMA_DIR = Path(__file__).resolve().parent.parent.parent / "schemas" / "jsonschema"


def _enum_oneof_json_to_java_title_map(schema: dict) -> Dict[str, str]:
    out: Dict[str, str] = {}
    one_of = schema.get("oneOf")
    if not isinstance(one_of, list):
        return out
    for branch in one_of:
        if not isinstance(branch, dict):
            continue
        title = branch.get("title")
        ev = branch.get("enum")
        if not isinstance(title, str) or not isinstance(ev, list) or len(ev) != 1:
            continue
        v = ev[0]
        if isinstance(v, str):
            out[v] = title
    return out


def _camel_to_screaming_snake(name: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9]+", "_", name)
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    if not sanitized:
        return ""
    result = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", sanitized)
    result = re.sub(r"([a-z])([A-Z])", r"\1_\2", result)
    result = re.sub(r"([A-Za-z])(\d)", r"\1_\2", result)
    result = re.sub(r"(\d)([A-Za-z])", r"\1_\2", result)
    result = result.upper()
    if result[0].isdigit():
        result = "_" + result
    return result


class SchemaIndex:
    """Lazy-loaded index over the 845+ CDM JSON Schema files."""

    def __init__(self, schema_dir: Optional[Path] = None) -> None:
        self._schema_dir = schema_dir or SCHEMA_DIR
        self._title_to_file: Dict[str, str] = {}
        self._file_to_meta: Dict[str, Dict[str, str]] = {}
        self._enum_files: Dict[str, List[str]] = {}
        self._built = False

    def _ensure_built(self) -> None:
        if self._built:
            return
        self._build()
        self._built = True

    def _build(self) -> None:
        for schema_path in self._schema_dir.glob("*.schema.json"):
            data = self._load_schema_file(schema_path.name)
            if data is None:
                continue

            title = data.get("title", "")
            anchor = data.get("$anchor", "")
            schema_type = data.get("type", "")

            if not title:
                continue

            self._title_to_file[title] = schema_path.name
            self._file_to_meta[schema_path.name] = {
                "title": title,
                "anchor": anchor,
                "type": schema_type,
            }

            enum_values = data.get("enum")
            if isinstance(enum_values, list) and schema_type == "string":
                self._enum_files[schema_path.name] = enum_values

    @lru_cache(maxsize=1024)
    def _load_schema_file(self, filename: str) -> Optional[dict]:
        path = self._schema_dir / filename
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            return json.loads(raw, strict=False)
        except (json.JSONDecodeError, OSError):
            return None

    # ------------------------------------------------------------------
    # 1.1  type_name → schema_file
    # ------------------------------------------------------------------

    def type_name_to_file(self, type_name: str) -> Optional[str]:
        """Resolve a CDM type name (e.g. 'Trade') to its schema filename."""
        self._ensure_built()
        direct = self._title_to_file.get(type_name)
        if direct:
            return direct
        lower_map = {k.lower(): v for k, v in self._title_to_file.items()}
        return lower_map.get(type_name.lower())

    def file_to_type_name(self, filename: str) -> Optional[str]:
        """Reverse lookup: schema filename → CDM type title."""
        self._ensure_built()
        meta = self._file_to_meta.get(filename)
        return meta["title"] if meta else None

    def all_type_names(self) -> List[str]:
        """Return all indexed CDM type names."""
        self._ensure_built()
        return sorted(self._title_to_file.keys())

    # ------------------------------------------------------------------
    # 1.2  schema_ref → Java class
    # ------------------------------------------------------------------

    def schema_ref_to_java_class(self, schema_ref: str) -> Optional[str]:
        """Resolve a schema $ref filename to a fully-qualified Java class name.

        Primary: parse the filename (reliable package structure).
        Fallback: $anchor + title from schema content.
        Returns None if the schema file does not exist.
        """
        self._ensure_built()
        from_filename = self._java_class_from_filename(schema_ref)
        if from_filename is not None:
            data = self._load_schema_file(schema_ref)
            if data is not None:
                return from_filename
        return None

    def java_class_parts(self, schema_ref: str) -> Tuple[str, str, str]:
        """Return (fully_qualified, package, simple_name) for a schema ref."""
        fq = self.schema_ref_to_java_class(schema_ref)
        if fq is None:
            return ("", "", "")
        dot = fq.rfind(".")
        if dot == -1:
            return (fq, "", fq)
        return (fq, fq[:dot], fq[dot + 1:])

    @staticmethod
    def _java_class_from_filename(filename: str) -> Optional[str]:
        """Fallback: derive Java class from filename convention.

        cdm-base-staticdata-party-Party.schema.json
        → parts = [cdm, base, staticdata, party, Party]
        → package = cdm.base.staticdata.party, class = Party
        """
        stem = filename.replace(".schema.json", "")
        if not stem:
            return None
        parts = stem.split("-")
        class_start = 0
        for i, part in enumerate(parts):
            if part and part[0].isupper():
                class_start = i
                break
        else:
            return ".".join(parts)

        package = ".".join(parts[:class_start])
        class_name = "".join(parts[class_start:])
        if package:
            return f"{package}.{class_name}"
        return class_name

    # ------------------------------------------------------------------
    # 1.3  Enum detection
    # ------------------------------------------------------------------

    def is_enum(self, schema_ref: str) -> bool:
        """Check if a schema file defines an enum type."""
        self._ensure_built()
        return schema_ref in self._enum_files

    def is_enum_by_name(self, type_name: str) -> bool:
        """Check if a CDM type name is an enum."""
        filename = self.type_name_to_file(type_name)
        if filename is None:
            return False
        return self.is_enum(filename)

    def enum_values(self, schema_ref: str) -> List[str]:
        """Return the JSON enum values for a schema file, or [] if not an enum."""
        self._ensure_built()
        return list(self._enum_files.get(schema_ref, []))

    def enum_values_by_name(self, type_name: str) -> List[str]:
        """Return enum values by CDM type name."""
        filename = self.type_name_to_file(type_name)
        if filename is None:
            return []
        return self.enum_values(filename)

    def enum_json_value_java_identifier(self, schema_ref: str, json_value: str) -> str:
        data = self._load_schema_file(schema_ref)
        if data is None:
            return _camel_to_screaming_snake(json_value)
        title_map = _enum_oneof_json_to_java_title_map(data)
        if json_value in title_map:
            return _camel_to_screaming_snake(title_map[json_value])
        return _camel_to_screaming_snake(json_value)

    def enum_java_constants(self, schema_ref: str) -> List[Dict[str, str]]:
        values = self.enum_values(schema_ref)
        return [
            {
                "json_value": v,
                "java_constant": self.enum_json_value_java_identifier(schema_ref, v),
            }
            for v in values
        ]

    def all_enum_names(self) -> List[str]:
        """Return all CDM enum type names."""
        self._ensure_built()
        names = []
        for filename in self._enum_files:
            meta = self._file_to_meta.get(filename)
            if meta:
                names.append(meta["title"])
        return sorted(names)

    # ------------------------------------------------------------------
    # Schema property introspection
    # ------------------------------------------------------------------

    def get_schema(self, type_name: str) -> Optional[dict]:
        """Load the full schema dict for a CDM type name."""
        filename = self.type_name_to_file(type_name)
        if filename is None:
            return None
        return self._load_schema_file(filename)

    def get_schema_by_ref(self, schema_ref: str) -> Optional[dict]:
        """Load the full schema dict for a schema $ref filename."""
        return self._load_schema_file(schema_ref)
