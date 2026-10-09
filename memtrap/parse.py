"""Turn a model reply into {answer, estimate, mechanism, confidence, reasoning}.

JSON first (the first balanced {...} object in the text, code fences
allowed), then a regex fallback over "key: value" pairs. Every field is
normalized on its own; a field that cannot be read is None. A reply where
neither answer, mechanism nor estimate can be read is a parse failure (None).
"""

from __future__ import annotations

import json
import re

LETTERS = ("A", "B", "C", "D")
FIELDS = ("answer", "estimate", "mechanism", "confidence", "reasoning")

# bytes per unit; sizes are binary
_UNIT_BYTES = {
    "b": 1, "byte": 1, "bytes": 1,
    "kb": 1 << 10, "kib": 1 << 10, "k": 1 << 10,
    "mb": 1 << 20, "mib": 1 << 20, "m": 1 << 20,
    "gb": 1 << 30, "gib": 1 << 30, "g": 1 << 30,
}
_NUMBER_UNIT = re.compile(r"(-?\d[\d,_]*(?:\.\d+)?|-?\.\d+)\s*([a-zA-Z]+)?")


def _json_objects(text: str):
    """Yield every balanced {...} substring, outermost first, left to right."""
    depth = 0
    start = None
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                yield text[start : i + 1]
                start = None


def _from_json(text: str) -> dict | None:
    for chunk in _json_objects(text):
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and any(k in obj for k in ("answer", "mechanism", "estimate")):
            return obj
    return None


_PAIR = re.compile(
    r"""["']?\b(answer|estimate|mechanism|confidence|reasoning)\b["']?\s*[:=]\s*("(?:[^"\\]|\\.)*"|[^,}\n]+)""",
    re.IGNORECASE,
)


def _from_pairs(text: str) -> dict | None:
    """Fallback for broken JSON and for "key: value" lines."""
    found: dict = {}
    for m in _PAIR.finditer(text):
        key = m.group(1).lower()
        if key not in found:
            found[key] = m.group(2).strip().strip('"').strip("'").strip()
    return found or None


def letter(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip().upper()
    m = re.fullmatch(r"(?:OPTION\s+)?\(?([ABCD])\)?[.:)]?(?:\s.*)?", s, re.DOTALL)
    return m.group(1) if m else None


def number(value, unit: str = "MB") -> float | None:
    """A number in the case unit. Strings with a size unit are converted."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value and abs(value) != float("inf") else None
    s = str(value).strip().lstrip("~≈").strip()
    m = _NUMBER_UNIT.search(s)
    if not m:
        return None
    num = float(m.group(1).replace(",", "").replace("_", ""))
    suffix = (m.group(2) or "").lower()
    if suffix in _UNIT_BYTES:
        return num * _UNIT_BYTES[suffix] / _UNIT_BYTES[unit.lower()]
    return num


def confidence(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        m = re.search(r"-?\d+(?:\.\d+)?", value)
        if not m:
            return None
        value = float(m.group(0))
    if not isinstance(value, (int, float)):
        return None
    if 0 <= value <= 100:
        return int(round(value))
    return None


def normalize(raw: dict, unit: str = "MB") -> dict | None:
    out = {
        "answer": letter(raw.get("answer")),
        "estimate": number(raw.get("estimate"), unit),
        "mechanism": letter(raw.get("mechanism")),
        "confidence": confidence(raw.get("confidence")),
        "reasoning": str(raw["reasoning"])[:2000] if raw.get("reasoning") is not None else None,
    }
    if out["answer"] is None and out["mechanism"] is None and out["estimate"] is None:
        return None
    return out


def parse_response(reply, unit: str = "MB") -> dict | None:
    """Parse a reply that may be a dict (structured output), an object with
    attributes, or text."""
    if reply is None:
        return None
    if isinstance(reply, dict):
        return normalize(reply, unit)
    if not isinstance(reply, str):
        attrs = {k: getattr(reply, k) for k in FIELDS if hasattr(reply, k)}
        if attrs:
            return normalize(attrs, unit)
        reply = str(reply)
    raw = _from_json(reply)
    if raw is not None:
        parsed = normalize(raw, unit)
        if parsed is not None:
            return parsed
    raw = _from_pairs(reply)
    return normalize(raw, unit) if raw else None
