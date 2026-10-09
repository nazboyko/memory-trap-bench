"""Loading the case catalog and the measured truth, and the small expression
language the catalog uses to turn raw readings into answers and estimates."""

from __future__ import annotations

import ast
import json
import operator
import string
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = ROOT / "cases" / "cases.json"
TRUTH_PATH = ROOT / "cases" / "truth.json"

LETTERS = ("A", "B", "C", "D")
# Sizes are binary: 1 MB = 2**20 bytes, 1 KB = 2**10 bytes.
UNITS = {"MB": 1 << 20, "KB": 1 << 10}

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


def load_cases(path: Path = CASES_PATH) -> list[dict]:
    with open(path) as fh:
        return json.load(fh)


def load_truth(path: Path = TRUTH_PATH) -> dict:
    if not Path(path).exists():
        return {}
    with open(path) as fh:
        return json.load(fh)


def active_cases(cases: list[dict]) -> list[dict]:
    """Cases that are part of the benchmark (reserves are left out)."""
    return [c for c in cases if c["status"] != "reserve"]


def by_id(cases: list[dict]) -> dict[str, dict]:
    return {c["id"]: c for c in cases}


def expr_names(expr: str) -> set[str]:
    tree = ast.parse(expr, mode="eval")
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def eval_expr(expr: str, names: dict[str, float]) -> float:
    """Evaluate +, -, *, / over numbers and names. Anything else is rejected."""

    def walk(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in names or names[node.id] is None:
                raise KeyError(f"unknown name {node.id!r} in {expr!r}")
            return float(names[node.id])
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -walk(node.operand)
        raise ValueError(f"unsupported expression {expr!r}")

    return walk(ast.parse(expr, mode="eval"))


def namespace(values: dict[str, float], n: int) -> dict[str, float]:
    return {**UNITS, "n": n, **values}


def quantities(case: dict, values: dict[str, float], n: int) -> dict[str, float]:
    ns = namespace(values, n)
    return {k: eval_expr(e, ns) for k, e in case["decision"]["quantities"].items()}


def decide(case: dict, values: dict[str, float], n: int) -> str | None:
    """Return the option whose rule matches the measured values, or None.

    A rule matches when every quantity it names lies in [lo, hi). The first
    matching rule wins. None means the measurement falls between the options,
    and the case has to be rewritten.
    """
    q = quantities(case, values, n)
    for rule in case["decision"]["rules"]:
        if all(lo <= q[name] < hi for name, (lo, hi) in rule["when"].items()):
            return rule["option"]
    return None


def estimate_value(case: dict, values: dict[str, float], n: int) -> float:
    return eval_expr(case["estimate"]["expr"], namespace(values, n))


def evidence_placeholders(text: str) -> list[str]:
    return [f for _, f, _, _ in string.Formatter().parse(text) if f]


def render_evidence(text: str, values: dict[str, float], n: int) -> str:
    """Fill {variant_MB}, {variant_KB} and {variant_KB_each} placeholders."""
    filled = {}
    for field in evidence_placeholders(text):
        if field.endswith("_KB_each"):
            filled[field] = f"{values[field[: -len('_KB_each')]] / n / UNITS['KB']:,.1f}"
        elif field.endswith("_MB"):
            filled[field] = f"{values[field[: -len('_MB')]] / UNITS['MB']:,.1f}"
        elif field.endswith("_KB"):
            filled[field] = f"{values[field[: -len('_KB')]] / UNITS['KB']:,.1f}"
        else:
            raise KeyError(f"unknown evidence placeholder {field!r}")
    return text.format(**filled)


def placeholder_variant(field: str) -> str:
    for suffix in ("_KB_each", "_MB", "_KB"):
        if field.endswith(suffix):
            return field[: -len(suffix)]
    raise KeyError(f"unknown evidence placeholder {field!r}")


def version_label(version: str) -> str:
    """'php-8.3' -> 'PHP 8.3', 'go-1.24' -> 'Go 1.24'."""
    runtime, _, number = version.partition("-")
    return {"php": "PHP", "go": "Go"}[runtime] + " " + number
