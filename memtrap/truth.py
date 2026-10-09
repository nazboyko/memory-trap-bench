"""Merge the measurement files into cases/truth.json.

    uv run python -m memtrap.truth

Reads every results/measurements/*.json report, groups readings by runtime
version (major.minor) and source, takes the median of the readings for each
case variant, and decides each case's answer with the rules in cases.json.
Readings from GitHub Actions (files named gha-*) are preferred; a local
reading is used only for a version that Actions did not measure.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from memtrap.catalog import ROOT, TRUTH_PATH, decide, estimate_value, load_cases, quantities

MEASUREMENTS = ROOT / "results" / "measurements"
SOURCE_RANK = {"gha": 0, "local": 1}


def version_key(report: dict) -> str:
    """'8.3.12' -> 'php-8.3', 'go1.24.9' -> 'go-1.24'."""
    m = re.search(r"(\d+)\.(\d+)", report["version"])
    if not m:
        raise ValueError(f"cannot read a version from {report['version']!r}")
    return f"{report['runtime']}-{m.group(1)}.{m.group(2)}"


def platform(report: dict) -> str:
    if report["runtime"] == "go":
        return f"{report['goos']}/{report['goarch']}"
    return f"{report.get('os', '?')}, {report.get('sapi', '?')}, {'ZTS' if report.get('thread_safe') else 'NTS'}, opcache {'on' if report.get('opcache') else 'off'}"


def source_of(path: Path) -> str:
    return "gha" if path.name.startswith("gha-") else "local"


def load_reports(directory: Path = MEASUREMENTS) -> list[tuple[Path, dict]]:
    out = []
    for path in sorted(directory.glob("*.json")):
        with open(path) as fh:
            out.append((path, json.load(fh)))
    return out


def group_readings(reports: list[tuple[Path, dict]]) -> dict:
    """{version_key: {source: {"runtime_versions", "platforms", "files",
    "values": {case: {variant: [values]}}, "n": {case: n}}}}"""
    groups: dict = defaultdict(lambda: defaultdict(lambda: {
        "runtime_versions": set(), "platforms": set(), "files": [],
        "values": defaultdict(lambda: defaultdict(list)), "n": {}, "metrics": {},
    }))
    for path, report in reports:
        g = groups[version_key(report)][source_of(path)]
        g["runtime_versions"].add(report["version"])
        g["platforms"].add(platform(report))
        g["files"].append(path.name)
        for r in report["readings"]:
            g["values"][r["case"]][r["variant"]].append(int(r["value"]))
            g["n"][r["case"]] = int(r["n"])
            g["metrics"].setdefault(r["case"], {})[r["variant"]] = r["metric"]
    return groups


def spread(values: list[int]) -> float:
    mid = statistics.median(values)
    if mid == 0:
        return 0.0 if max(values) == min(values) else float("inf")
    return (max(values) - min(values)) / abs(mid)


def build_truth(cases: list[dict], groups: dict) -> dict:
    truth: dict = {
        "_meta": {
            "generated_by": "python -m memtrap.truth",
            "values": "bytes, median of the readings of one source",
            "units": "MB = 1,048,576 bytes, KB = 1,024 bytes",
            "source_preference": "gha (GitHub Actions, linux/amd64) over local",
        }
    }
    for case in cases:
        cid = case["id"]
        versions: dict = {}
        for vkey in sorted(groups):
            if not vkey.startswith(case["runtime"] + "-"):
                continue
            sources = groups[vkey]
            usable = [s for s in sorted(sources, key=SOURCE_RANK.get) if cid in sources[s]["values"]]
            if not usable:
                continue
            src = usable[0]
            g = sources[src]
            raw = g["values"][cid]
            missing = set(case["variants"]) - set(raw)
            if missing:
                raise SystemExit(f"{cid} on {vkey} ({src}): no readings for variants {sorted(missing)}")
            values = {v: int(statistics.median(xs)) for v, xs in raw.items()}
            n = g["n"][cid]
            entry = {
                "runtime_versions": sorted(g["runtime_versions"]),
                "source": src,
                "platform": sorted(g["platforms"]),
                "readings": min(len(xs) for xs in raw.values()),
                "n": n,
                "metrics": g["metrics"][cid],
                "values": values,
                "spread": {v: round(spread(xs), 4) for v, xs in raw.items()},
                "quantities": {k: round(q, 4) for k, q in quantities(case, values, n).items()},
                "answer": decide(case, values, n),
                "estimate": round(estimate_value(case, values, n), 2),
                "estimate_unit": case["estimate"]["unit"],
                "files": sorted(g["files"]),
            }
            versions[vkey] = entry
        asked = case["versions"]
        answers = {v: versions[v]["answer"] for v in asked if v in versions}
        complete = len(answers) == len(asked) and None not in answers.values()
        split = len(set(answers.values())) > 1 if complete else None
        if not complete:
            cold = None
        elif split:
            cold = case["version_option"]
        else:
            cold = next(iter(answers.values()))
        truth[cid] = {
            "metric": case["estimate"]["metric"],
            "asked_versions": asked,
            "complete": complete,
            "version_split": split,
            "cold_answer": cold,
            "versions": versions,
        }
    return truth


def summary_lines(cases: list[dict], truth: dict) -> list[str]:
    lines = []
    for case in cases:
        t = truth[case["id"]]
        for vkey, e in t["versions"].items():
            mark = "*" if vkey in t["asked_versions"] else " "
            q = ", ".join(f"{k}={v}" for k, v in e["quantities"].items())
            lines.append(f"{case['id']:3} {mark}{vkey:9} {e['source']:5} answer={e['answer'] or '-'} "
                         f"estimate={e['estimate']} {e['estimate_unit']}  {q}  spread={max(e['spread'].values())}")
        lines.append(f"{case['id']:3}  cold={t['cold_answer']} split={t['version_split']} complete={t['complete']}")
    return lines


def main(argv: list[str]) -> int:
    cases = load_cases()
    reports = load_reports()
    if not reports:
        print("no measurement files under results/measurements", file=sys.stderr)
        return 1
    truth = build_truth(cases, group_readings(reports))
    out = Path(argv[1]) if len(argv) > 1 else TRUTH_PATH
    out.write_text(json.dumps(truth, indent=2, sort_keys=False) + "\n")
    for line in summary_lines(cases, truth):
        print(line)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
