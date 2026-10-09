"""Write the three Kaggle task files from cases.json and truth.json.

    uv run python -m memtrap.build_tasks            # kaggle/memtrap_{cold,versioned,evidence}.py
    uv run python -m memtrap.build_tasks --pilot 3  # kaggle/memtrap_pilot.py, cold, first 3 items

The builder refuses to run when the catalog does not validate, when a case is
not measured, or when truth is missing for a version a prompt names.
"""

from __future__ import annotations

import pprint
import sys
from pathlib import Path

from memtrap.catalog import ROOT, active_cases, by_id, load_cases, load_truth, render_evidence
from memtrap.prompts import CONDITIONS, render_prompt
from memtrap.task_template import TEMPLATE
from memtrap.validate import validate

OUT_DIR = ROOT / "kaggle"
REPO_URL = "https://github.com/nazboyko/memory-trap-bench"
ITEM_TASK_NAME = "MemoryTrap Item"

TASKS = {
    "cold": {
        "file": "memtrap_cold.py",
        "name": "MemoryTrap Cold",
        "function": "memtrap_cold",
        "title": "MemoryTrap Bench: cold (code only)",
        "description": (
            "Seventeen questions about memory use in PHP arrays, Go maps and goroutine stacks, asked with "
            "the code only. Each reply is scored against numbers measured on pinned runtime versions: "
            "the answer option, an estimate within 0.67x to 1.5x of the measured value, and the "
            "mechanism, one third each. Three cases are controls where the rule of thumb holds."
        ),
    },
    "versioned": {
        "file": "memtrap_versioned.py",
        "name": "MemoryTrap Versioned",
        "function": "memtrap_versioned",
        "title": "MemoryTrap Bench: versioned (code and runtime version)",
        "description": (
            "The MemoryTrap Bench questions with the runtime version added (PHP 8.1 or 8.3, Go 1.23 or "
            "1.24). Cases whose measured answer differs between versions are asked once per version. "
            "Scored like the cold task: answer, estimate within 0.67x to 1.5x, mechanism."
        ),
    },
    "evidence": {
        "file": "memtrap_evidence.py",
        "name": "MemoryTrap Evidence",
        "function": "memtrap_evidence",
        "title": "MemoryTrap Bench: evidence (version and one measured fact)",
        "description": (
            "The versioned MemoryTrap Bench questions plus one measured fact from another case of the "
            "same family, which contradicts the family's rule of thumb without giving the answer. "
            "Does a real number move the answer? Scored like the cold task."
        ),
    },
}
PILOT = {
    "file": "memtrap_pilot.py",
    "name": "MemoryTrap Pilot A",
    "function": "memtrap_pilot",
    "title": "MemoryTrap Bench: pilot",
    "description": TASKS["cold"]["description"],
}


class BuildError(Exception):
    pass


def item_versions(case: dict, truth_case: dict, condition: str) -> list[str | None]:
    if condition == "cold":
        return [None]
    if truth_case["version_split"]:
        return list(case["versions"])
    return [case["versions"][-1]]


def build_items(cases: list[dict], truth: dict, condition: str) -> dict[str, dict]:
    index = by_id(cases)
    items: dict[str, dict] = {}
    for case in active_cases(cases):
        cid = case["id"]
        if case["status"] != "measured":
            raise BuildError(f"{cid} is {case['status']}, not measured")
        t = truth.get(cid)
        if not t or not t.get("complete") or t.get("cold_answer") is None:
            raise BuildError(f"{cid} has no complete truth")
        for v in case["versions"]:
            if v not in t["versions"]:
                raise BuildError(f"{cid} has no truth for {v}")
        for version in item_versions(case, t, condition):
            evidence = None
            if condition == "evidence":
                src = index[case["evidence"]["from"]]
                st = truth.get(src["id"], {}).get("versions", {}).get(version)
                if st is None:
                    raise BuildError(f"{cid}: evidence source {src['id']} has no truth for {version}")
                evidence = render_evidence(case["evidence"]["text"], st["values"], st["n"])
            item_id = cid if version is None else f"{cid}@{version}"
            answer = t["cold_answer"] if version is None else t["versions"][version]["answer"]
            estimates = [t["versions"][v]["estimate"] for v in case["versions"]] if version is None else [
                t["versions"][version]["estimate"]
            ]
            lo, hi = case["estimate"]["tolerance"]
            unit = case["estimate"]["unit"]
            measured = " or ".join(f"{e:g}" for e in sorted(set(estimates)))
            items[item_id] = {
                "case_id": cid,
                "condition": condition,
                "version": version,
                "unit": unit,
                "prompt": render_prompt(case, condition, version, evidence),
                "case": {
                    "id": cid,
                    "control": case["control"],
                    "correct_mechanism": case["correct_mechanism"],
                    "folklore_mechanism": case["folklore_mechanism"],
                    "folklore_answer": case["folklore_answer"],
                    "versions": case["versions"],
                    "estimate": {"tolerance": case["estimate"]["tolerance"]},
                },
                "truth": {
                    "cold_answer": t["cold_answer"],
                    "versions": {
                        v: {"answer": t["versions"][v]["answer"], "estimate": t["versions"][v]["estimate"]}
                        for v in case["versions"]
                    },
                },
                "expect_answer": f"{item_id} answer {answer}: {case['options'][answer]}",
                "expect_mechanism": f"{item_id} mechanism {case['correct_mechanism']}",
                "expect_estimate": f"{item_id} estimate within {lo}x-{hi}x of the measured {measured} {unit}",
            }
    return items


def module_source(path: Path) -> str:
    """The source of a memtrap module without its docstring and future import."""
    text = path.read_text()
    marker = "from __future__ import annotations\n"
    if marker not in text:
        raise BuildError(f"{path.name} has no future import to cut at")
    return text.split(marker, 1)[1].strip() + "\n"


def render_task(spec: dict, condition: str, items: dict, limit: int | None = None) -> str:
    return TEMPLATE.format(
        title=spec["title"],
        description=spec["description"],
        repo_url=REPO_URL,
        condition=condition,
        item_limit=limit,
        items=pprint.pformat(items, width=110, sort_dicts=False),
        parse_source=module_source(ROOT / "memtrap" / "parse.py"),
        score_source=module_source(ROOT / "memtrap" / "score.py"),
        item_task_name=ITEM_TASK_NAME,
        task_name=spec["name"],
        function_name=spec["function"],
    )


def check_catalog() -> tuple[list[dict], dict]:
    errs, _ = validate(check_syntax=False)
    if errs:
        raise BuildError("catalog does not validate:\n  " + "\n  ".join(errs))
    return load_cases(), load_truth()


def main(argv: list[str]) -> int:
    try:
        cases, truth = check_catalog()
        OUT_DIR.mkdir(exist_ok=True)
        if "--pilot" in argv:
            limit = int(argv[argv.index("--pilot") + 1])
            items = build_items(cases, truth, "cold")
            out = OUT_DIR / PILOT["file"]
            out.write_text(render_task(PILOT, "cold", items, limit))
            print(f"wrote {out.relative_to(ROOT)} (cold, first {limit} of {len(items)} items)")
            return 0
        for condition in CONDITIONS:
            spec = TASKS[condition]
            items = build_items(cases, truth, condition)
            out = OUT_DIR / spec["file"]
            out.write_text(render_task(spec, condition, items))
            print(f"wrote {out.relative_to(ROOT)} ({len(items)} items)")
    except BuildError as exc:
        print(f"build refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
