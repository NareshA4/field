"""Per-category custom attributes (project decision D-059: "categories per organization + custom attributes so a laptop
is not a pump"). A category declares a short list of attribute definitions; an asset of that category stores the
values in ``Asset.attributes`` (JSON object, key -> normalized string). Everything is validated here, on the server.

Definition (stored in ``AssetCategory.attribute_definitions``):
    {"key": "voltage", "label": "Voltage", "type": "number", "required": false, "choices": []}
Text form used by the category screen, one attribute per line:
    Label | type | required | choice 1, choice 2
(only the label is mandatory; ``type`` is text | number | date | choice)."""
from __future__ import annotations

import datetime
import re
from decimal import Decimal, InvalidOperation

from apps.core.exceptions import ValidationFailed

TYPES = ("text", "number", "date", "choice")
MAX_DEFINITIONS = 20
MAX_TEXT = 200
_TRUE = {"required", "yes", "true", "1", "*", "y"}


def make_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40]


def _choices(raw) -> list[str]:
    if isinstance(raw, str):
        raw = raw.split(",")
    out, seen = [], set()
    for c in raw or []:
        c = str(c).strip()
        if c and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c[:80])
    return out


def normalize_definitions(raw) -> list[dict]:
    """Validates a list of definition dicts (API / parsed text) and returns the canonical stored form."""
    if raw is None:
        return []
    if not isinstance(raw, list | tuple):
        raise ValidationFailed("Attribute definitions must be a list.", code="invalid_attribute_definitions")
    if len(raw) > MAX_DEFINITIONS:
        raise ValidationFailed(f"A category can define at most {MAX_DEFINITIONS} attributes.",
                               code="too_many_attributes")
    out, keys = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValidationFailed("Each attribute definition must be an object.", code="invalid_attribute_definitions")
        label = str(item.get("label", "")).strip()
        if not label or len(label) > 60:
            raise ValidationFailed("Each attribute needs a label of at most 60 characters.", code="invalid_attribute")
        key = make_key(label)
        if not key:
            raise ValidationFailed(f"'{label}' is not a usable attribute name.", code="invalid_attribute")
        if key in keys:
            raise ValidationFailed(f"Attribute '{label}' is defined twice.", code="duplicate_attribute")
        keys.add(key)
        type_ = str(item.get("type") or "text").strip().lower()
        if type_ not in TYPES:
            raise ValidationFailed(f"Unknown attribute type '{type_}' (use {', '.join(TYPES)}).",
                                   code="invalid_attribute_type")
        choices = _choices(item.get("choices")) if type_ == "choice" else []
        if type_ == "choice" and len(choices) < 2:
            raise ValidationFailed(f"Attribute '{label}' needs at least two choices.", code="invalid_attribute")
        out.append({"key": key, "label": label, "type": type_, "required": bool(item.get("required")),
                    "choices": choices})
    return out


def parse_text(text: str) -> list[dict]:
    """``Label | type | required | a, b`` lines -> definitions (validated)."""
    raw = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split("|")]
        item = {"label": parts[0], "type": (parts[1] if len(parts) > 1 and parts[1] else "text").lower(),
                "required": len(parts) > 2 and parts[2].lower() in _TRUE,
                "choices": parts[3] if len(parts) > 3 else ""}
        raw.append(item)
    return normalize_definitions(raw)


def to_text(definitions: list[dict]) -> str:
    lines = []
    for d in definitions or []:
        lines.append(" | ".join([d["label"], d["type"], "required" if d.get("required") else "",
                                 ", ".join(d.get("choices") or [])]).rstrip(" |"))
    return "\n".join(lines)


def clean_values(definitions: list[dict], values: dict | None) -> dict:
    """Validates ``values`` against a category's definitions; returns the normalized {key: string} to store.
    Unknown keys are rejected (a typo must not silently disappear)."""
    values = values or {}
    if not isinstance(values, dict):
        raise ValidationFailed("Attributes must be an object of key / value pairs.", code="invalid_attributes")
    defined = {d["key"]: d for d in definitions or []}
    unknown = sorted(set(values) - set(defined))
    if unknown:
        raise ValidationFailed(f"Unknown attribute(s) for this category: {', '.join(unknown)}.",
                               code="unknown_attribute")
    out = {}
    for key, d in defined.items():
        raw = values.get(key)
        text = "" if raw is None else str(raw).strip()
        if not text:
            if d.get("required"):
                raise ValidationFailed(f"{d['label']} is required for this category.", code="attribute_required")
            continue
        if d["type"] == "number":
            try:
                number = Decimal(text)
            except InvalidOperation as exc:
                raise ValidationFailed(f"{d['label']} must be a number.", code="invalid_attribute_value") from exc
            if not number.is_finite():
                raise ValidationFailed(f"{d['label']} must be a number.", code="invalid_attribute_value")
            text = format(number, "f")
        elif d["type"] == "date":
            try:
                text = datetime.date.fromisoformat(text).isoformat()
            except ValueError as exc:
                raise ValidationFailed(f"{d['label']} must be a date (YYYY-MM-DD).",
                                       code="invalid_attribute_value") from exc
        elif d["type"] == "choice":
            match = next((c for c in d["choices"] if c.lower() == text.lower()), None)
            if match is None:
                raise ValidationFailed(f"{d['label']} must be one of: {', '.join(d['choices'])}.",
                                       code="invalid_attribute_value")
            text = match
        elif len(text) > MAX_TEXT:
            raise ValidationFailed(f"{d['label']} is limited to {MAX_TEXT} characters.",
                                   code="invalid_attribute_value")
        out[key] = text
    return out


def display(definitions: list[dict], values: dict | None) -> list[tuple[str, str]]:
    """[(label, value)] for the asset detail page, in definition order (values of removed definitions are hidden)."""
    values = values or {}
    return [(d["label"], values[d["key"]]) for d in definitions or [] if values.get(d["key"])]
