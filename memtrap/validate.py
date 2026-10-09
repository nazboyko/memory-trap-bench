"""Validate cases/cases.json, and cases/truth.json when it exists.

    uv run python -m memtrap.validate
    python3 -m memtrap.validate --syntax-only   # snippets only (CI lint step)

Exit code 0 when the catalog is valid, 1 otherwise. Only the standard library
is used, so the CI jobs can run it without installing anything.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import re

from memtrap.catalog import (
    CASES_PATH,
    LETTERS,
    TRUTH_PATH,
    UNITS,
    active_cases,
    by_id,
    eval_expr,
    evidence_placeholders,
    expr_names,
    load_cases,
    load_truth,
    placeholder_variant,
    render_evidence,
)

REQUIRED = {
    "id": str, "runtime": str, "family": str, "control": bool, "status": str,
    "title": str, "snippet_a": str, "snippet_b": str, "question": str,
    "options": dict, "version_option": (str, type(None)), "folklore_answer": str,
    "mechanisms": dict, "correct_mechanism": str, "folklore_mechanism": str,
    "variants": dict, "decision": dict, "estimate": dict, "versions": list,
    "evidence": dict, "why_it_matters": str, "folklore_line": str,
}
FAMILIES = {"php": {"arrays"}, "go": {"maps", "stacks"}}
STATUSES = {"draft", "measured", "reserve"}
BANNED_WORDS = ("trap", "surprising", "actually", "contrary to")
RESERVED_NAMES = {"n", *UNITS}
GO_MAIN_MARKER = "// in main"


def check_case(case: dict, all_cases: dict[str, dict]) -> list[str]:
    errs: list[str] = []
    cid = case.get("id", "?")

    def err(msg: str) -> None:
        errs.append(f"{cid}: {msg}")

    for field, kind in REQUIRED.items():
        if field not in case:
            err(f"missing field {field}")
        elif not isinstance(case[field], kind):
            err(f"field {field} has type {type(case[field]).__name__}")
    if errs:
        return errs

    if case["runtime"] not in FAMILIES:
        err(f"unknown runtime {case['runtime']}")
        return errs
    if case["family"] not in FAMILIES[case["runtime"]]:
        err(f"family {case['family']} does not belong to {case['runtime']}")
    if case["status"] not in STATUSES:
        err(f"unknown status {case['status']}")

    for name in ("options", "mechanisms"):
        if tuple(sorted(case[name])) != LETTERS:
            err(f"{name} must have exactly the keys A, B, C, D")
        elif any(not str(v).strip() for v in case[name].values()):
            err(f"{name} has an empty text")
    for name in ("folklore_answer", "correct_mechanism", "folklore_mechanism"):
        if case[name] not in LETTERS:
            err(f"{name} must be one of A-D")
    if errs:
        return errs

    if case["control"]:
        if case["correct_mechanism"] != case["folklore_mechanism"]:
            err("control case: the folklore mechanism must be the correct one")
    else:
        if case["correct_mechanism"] == case["folklore_mechanism"]:
            err("the folklore mechanism must differ from the correct one")
        lengths = {k: len(v) for k, v in case["mechanisms"].items()}
        mine = lengths[case["correct_mechanism"]]
        others = [n for k, n in lengths.items() if k != case["correct_mechanism"]]
        if mine >= max(others):
            err("the correct mechanism is the longest text; it gives the answer away")
        if mine < min(others):
            err("the correct mechanism is the shortest text; it gives the answer away")

    vo = case["version_option"]
    if vo is not None:
        if vo not in LETTERS:
            err("version_option must be one of A-D or null")
        elif "depends" not in case["options"][vo].lower():
            err("the version option must say that the answer depends on the version")
        if len(case["versions"]) < 2:
            err("a case with a version option needs at least two versions")
        if case["folklore_answer"] == vo:
            err("the folklore answer cannot be the version option")
    else:
        for k, v in case["options"].items():
            if "depends on the" in v.lower():
                err(f"option {k} talks about a version split but version_option is null")

    text = " ".join([case["question"], *case["options"].values(), *case["mechanisms"].values()]).lower()
    for word in BANNED_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", text):
            err(f"question, options or mechanisms use the word {word!r}")

    variants = set(case["variants"])
    if variants & RESERVED_NAMES:
        err(f"variant names {sorted(variants & RESERVED_NAMES)} are reserved")
    dummy = {name: 1.0 for name in variants | RESERVED_NAMES}
    allowed = variants | {"n", *UNITS}
    dec = case["decision"]
    qnames = set(dec.get("quantities", {}))
    if not qnames:
        err("decision has no quantities")
    for qn, expr in dec.get("quantities", {}).items():
        try:
            unknown = expr_names(expr) - allowed
        except SyntaxError:
            err(f"quantity {qn} does not parse")
            continue
        if unknown:
            err(f"quantity {qn} uses unknown names {sorted(unknown)}")
            continue
        try:
            eval_expr(expr, dummy)
        except (ValueError, KeyError) as exc:
            err(f"quantity {qn}: {exc}")
    rule_options = []
    for rule in dec.get("rules", []):
        opt = rule.get("option")
        rule_options.append(opt)
        if opt not in LETTERS:
            err(f"rule option {opt!r} is not A-D")
        if opt is not None and opt == vo:
            err("the version option cannot be decided by a measurement rule")
        when = rule.get("when", {})
        if not when:
            err(f"rule for {opt} has no conditions")
        for qn, bounds in when.items():
            if qn not in qnames:
                err(f"rule for {opt} uses unknown quantity {qn}")
            if not (isinstance(bounds, list) and len(bounds) == 2 and bounds[0] < bounds[1]):
                err(f"rule for {opt} has a bad interval for {qn}")
    if len(set(rule_options)) != len(rule_options):
        err("two rules decide the same option")
    rules = dec.get("rules", [])
    for i, r1 in enumerate(rules):
        for r2 in rules[i + 1 :]:
            if rules_overlap(r1.get("when", {}), r2.get("when", {})):
                err(f"rules for {r1.get('option')} and {r2.get('option')} overlap")
    if case["folklore_answer"] != vo and case["folklore_answer"] not in rule_options:
        err("the folklore answer has no measurement rule")

    est = case["estimate"]
    if est.get("unit") not in UNITS:
        err("estimate unit must be MB or KB")
    try:
        unknown = expr_names(est.get("expr", "")) - allowed
        if unknown:
            err(f"estimate uses unknown names {sorted(unknown)}")
        else:
            eval_expr(est.get("expr", ""), dummy)
    except SyntaxError:
        err("estimate expression does not parse")
    except (ValueError, KeyError) as exc:
        err(f"estimate: {exc}")
    tol = est.get("tolerance")
    if not (isinstance(tol, list) and len(tol) == 2 and 0 < tol[0] < 1 < tol[1]):
        err("estimate tolerance must be [lo, hi] with lo < 1 < hi")
    if not est.get("metric") or not est.get("what"):
        err("estimate needs a metric and a description")

    for v in case["versions"]:
        if not str(v).startswith(case["runtime"] + "-"):
            err(f"version {v} does not belong to {case['runtime']}")

    ev = case["evidence"]
    src = all_cases.get(ev.get("from", ""))
    if src is None:
        err(f"evidence source {ev.get('from')!r} does not exist")
    else:
        if src["id"] == cid:
            err("evidence must come from a different case")
        if src["runtime"] != case["runtime"]:
            err("evidence must come from the same runtime")
        for field in evidence_placeholders(ev.get("text", "")):
            try:
                variant = placeholder_variant(field)
            except KeyError:
                err(f"unknown evidence placeholder {field}")
                continue
            if variant not in src["variants"]:
                err(f"evidence placeholder {field} names no variant of {src['id']}")
        try:
            render_evidence(ev.get("text", ""), {k: 1.0 for k in src["variants"]}, 1)
        except (KeyError, ValueError, IndexError) as exc:
            err(f"evidence text does not render: {exc!r}")
    if not ev.get("text"):
        err("evidence text is empty")
    return errs


def rules_overlap(w1: dict, w2: dict) -> bool:
    """Two rules overlap when their boxes intersect; a quantity a rule does
    not name is unbounded for that rule."""
    for name in set(w1) | set(w2):
        lo1, hi1 = w1.get(name, [float("-inf"), float("inf")])
        lo2, hi2 = w2.get(name, [float("-inf"), float("inf")])
        if max(lo1, lo2) >= min(hi1, hi2):
            return False
    return True


def check_truth(cases: list[dict], truth: dict) -> list[str]:
    errs: list[str] = []
    index = by_id(cases)
    for case in active_cases(cases):
        cid = case["id"]
        if case["status"] != "measured":
            continue  # drafts are allowed; the task builder refuses them
        t = truth.get(cid)
        if t is None:
            errs.append(f"{cid}: marked measured but missing from truth.json")
            continue
        answers = {}
        for v in case["versions"]:
            tv = t.get("versions", {}).get(v)
            if tv is None:
                errs.append(f"{cid}: no truth for {v}")
                continue
            if tv.get("answer") is None:
                errs.append(f"{cid}: measurement on {v} matches no option")
                continue
            if not isinstance(tv.get("estimate"), (int, float)):
                errs.append(f"{cid}: no numeric estimate for {v}")
            answers[v] = tv["answer"]
        if len(answers) != len(case["versions"]):
            continue
        split = len(set(answers.values())) > 1
        expected_cold = case["version_option"] if split else next(iter(answers.values()))
        if t.get("cold_answer") != expected_cold:
            errs.append(f"{cid}: cold answer {t.get('cold_answer')} should be {expected_cold}")
        if split and case["version_option"] is None:
            errs.append(f"{cid}: versions disagree {answers} but the case has no version option")
        if not split and case["version_option"] is not None:
            errs.append(f"{cid}: versions agree {answers} but the case offers a version option")
        newest = answers[case["versions"][-1]]
        if case["control"]:
            if any(a != case["folklore_answer"] for a in answers.values()):
                errs.append(f"{cid}: control case, but the folklore answer is not the measured one {answers}")
        elif case["folklore_answer"] in {expected_cold, newest}:
            # The rule of thumb may have been right on an older version (a
            # stale rule); it must be wrong for the cold and the newest answer.
            errs.append(f"{cid}: the folklore answer is the measured one {answers}; this is a control")
        lengths = {k: len(text) for k, text in case["options"].items()}
        for a in {expected_cold, newest} - {None}:
            if lengths[a] > max(n for k, n in lengths.items() if k != a):
                errs.append(f"{cid}: the measured answer {a} is the longest option text")
        src = case["evidence"]["from"]
        for v in case["versions"]:
            src_truth = truth.get(src, {}).get("versions", {}).get(v)
            if src_truth is None:
                errs.append(f"{cid}: evidence source {src} has no truth for {v}")
                continue
            if "values" not in src_truth:
                continue
            leak = evidence_echo(case, t["versions"][v], index[src], src_truth)
            if leak:
                errs.append(f"{cid}: the evidence on {v} states {leak}, inside the estimate tolerance; copying it would score")
    return errs


def evidence_echo(case: dict, target: dict, src_case: dict, src_truth: dict) -> list[float]:
    """Numbers in the rendered evidence that fall inside the target's
    estimate tolerance."""
    text = render_evidence(case["evidence"]["text"], src_truth["values"], src_truth["n"])
    lo, hi = case["estimate"]["tolerance"]
    goal = target["estimate"]
    numbers = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.\d+", text)]
    return [x for x in numbers if lo * goal <= x <= hi * goal]


def syntax_errors(cases: list[dict]) -> tuple[list[str], list[str]]:
    """Check snippets: gofmt -e for Go, php -l for PHP when php is installed."""
    errs: list[str] = []
    skipped: list[str] = []
    gofmt = shutil.which("gofmt")
    php = shutil.which("php")
    with tempfile.TemporaryDirectory() as tmp:
        for case in cases:
            for key in ("snippet_a", "snippet_b"):
                code = case[key]
                if not code.strip():
                    continue
                if case["runtime"] == "go":
                    if not gofmt:
                        skipped.append(f"{case['id']} {key}: gofmt not found")
                        continue
                    src = Path(tmp) / f"{case['id']}_{key}.go"
                    src.write_text(go_program(code))
                    res = subprocess.run([gofmt, "-e", "-l", str(src)], capture_output=True, text=True)
                else:
                    if not php:
                        skipped.append(f"{case['id']} {key}: php not installed")
                        continue
                    src = Path(tmp) / f"{case['id']}_{key}.php"
                    src.write_text("<?php\n" + code + "\n")
                    res = subprocess.run([php, "-l", str(src)], capture_output=True, text=True)
                if res.returncode != 0:
                    errs.append(f"{case['id']} {key}: {res.stderr.strip() or res.stdout.strip()}")
    return errs, skipped


def go_program(snippet: str) -> str:
    """Wrap a Go snippet into a file gofmt can parse. Text before a
    '// in main' line is top level; the rest goes into main()."""
    if GO_MAIN_MARKER in snippet:
        top, _, body = snippet.partition(GO_MAIN_MARKER)
        body = body.split("\n", 1)[1] if "\n" in body else ""
    elif snippet.lstrip().startswith(("func ", "type ", "//go:")):
        top, body = snippet, ""
    else:
        top, body = "", snippet
    return f"package main\n\n{top}\n\nfunc main() {{\n{body}\n}}\n"


def validate(cases_path: Path = CASES_PATH, truth_path: Path = TRUTH_PATH, check_syntax: bool = True) -> tuple[list[str], list[str]]:
    cases = load_cases(cases_path)
    errs: list[str] = []
    if not isinstance(cases, list) or not cases:
        return ["cases.json must be a non-empty list"], []
    ids = [c.get("id") for c in cases]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        errs.append(f"duplicate ids {sorted(dupes)}")
    index = by_id(cases)
    for case in cases:
        errs.extend(check_case(case, index))
    if errs:
        return errs, []
    active = active_cases(cases)
    controls = [c["id"] for c in active if c["control"]]
    if len(controls) != 3:
        errs.append(f"the benchmark needs exactly three control cases, found {controls}")
    for case in active:
        if index[case["evidence"]["from"]]["status"] == "reserve":
            errs.append(f"{case['id']}: evidence comes from a reserve case")
    errs.extend(check_truth(cases, load_truth(truth_path)))
    skipped: list[str] = []
    if check_syntax:
        syn, skipped = syntax_errors(cases)
        errs.extend(syn)
    return errs, skipped


def main(argv: list[str]) -> int:
    if "--syntax-only" in argv:
        errs, skipped = syntax_errors(load_cases())
        for line in errs:
            print(f"error: {line}")
        print(f"syntax: {len(errs)} error(s), {len(skipped)} snippet(s) skipped")
        return 1 if errs else 0
    errs, skipped = validate()
    cases = load_cases()
    active = active_cases(cases)
    if skipped:
        print(f"skipped syntax check for {len(skipped)} snippet(s): {sorted(set(s.split(': ', 1)[1] for s in skipped))}")
    if errs:
        for line in errs:
            print(f"error: {line}")
        print(f"{len(errs)} problem(s) in {len(cases)} cases")
        return 1
    measured = sum(c["status"] == "measured" for c in active)
    print(f"ok: {len(active)} benchmark cases ({measured} measured), {len(cases) - len(active)} reserves")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
