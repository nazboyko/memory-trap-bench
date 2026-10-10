"""Read the Kaggle run files and the measured truth, write results/REPORT.md
and the charts in results/charts/.

    uv run python -m memtrap.analyze
    uv run python -m memtrap.analyze --runs DIR --out DIR --banner "STUB DATA"

Run files are found under <runs>/{cold,versioned,evidence}/**/*.run.json. Every
reply is parsed and scored again from the raw text with memtrap.parse and
memtrap.score, so the report does not depend on the scores stored in the run
files; a disagreement between the two is reported. A model is scored on a
condition only from a run in which every item got a reply; when it has
several, the run on the newest task version counts and the others are used
to measure how much answers change between runs. The human quiz is never
read here.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from memtrap.build_tasks import TASKS, build_items
from memtrap.catalog import ROOT, active_cases, by_id, load_cases, load_truth, version_label
from memtrap.parse import parse_response
from memtrap.prompts import CONDITIONS
from memtrap.score import score_response, summarize

TASK_NAMES = {spec["name"]: cond for cond, spec in TASKS.items()}
RESULTS = ROOT / "results"

# Reference palette (light surface); categorical slots in fixed order.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
SEQ = ["#f0efec", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


# ------------------------------------------------------------------ loading
def run_condition(path: Path, data: dict, runs_root: Path) -> str | None:
    name = data.get("taskVersion", {}).get("name")
    if name in TASK_NAMES:
        return TASK_NAMES[name]
    try:
        top = path.relative_to(runs_root).parts[0]
    except ValueError:
        return None
    return top if top in CONDITIONS else None


def task_score(data: dict) -> float:
    for r in data.get("results", []):
        if "numericResult" in r:
            return float(r["numericResult"].get("value", 0.0))  # proto3 drops a 0.0
    return 0.0


def role_texts(run: dict, role: str) -> list[str]:
    texts = []
    for conv in run.get("conversations", []):
        for req in conv.get("requests", []):
            for content in req.get("contents", []):
                if content.get("role") == role:
                    texts.append("".join(p.get("text", "") for p in content.get("parts", [])))
    return texts


def assistant_texts(run: dict) -> list[str]:
    return role_texts(run, "CONTENT_ROLE_ASSISTANT")


def end_time(data: dict) -> datetime:
    raw = data.get("endTime", "")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


OUTPUT_CAP = 32768  # max_tokens the tasks send from version 2 on (memtrap/task_template.py)


def usage(run: dict) -> dict:
    """Output tokens and cost in USD of one item, and its longest single reply."""
    tokens = 0
    nano = 0
    longest = 0
    for conv in run.get("conversations", []):
        m = conv.get("metrics", {})
        tokens += int(m.get("outputTokens", 0))
        nano += int(m.get("inputTokensCostNanodollars", 0)) + int(m.get("outputTokensCostNanodollars", 0))
        replies = [int(req.get("metrics", {}).get("outputTokens", 0)) for req in conv.get("requests", [])]
        longest = max([longest, *replies] if replies else [longest, int(m.get("outputTokens", 0))])
    return {"tokens": tokens, "cost": nano / 1e9, "longest": longest}


def dict_result(run: dict) -> dict | None:
    for r in run.get("results", []):
        if "dictResult" in r:
            return r["dictResult"]
    return None


def run_layout(path: Path, runs_root: Path) -> tuple[str | None, int]:
    """(model slug, task version) from the layout `kaggle b t download` writes:
    <condition>/<task slug>/<task version>/<model slug>/<run id>/<file>."""
    try:
        parts = path.relative_to(runs_root).parts
    except ValueError:
        return None, 0
    if len(parts) == 6 and parts[2].isdigit():
        return parts[3], int(parts[2])
    return None, 0


def load_runs(runs_root: Path) -> list[dict]:
    """Every run file of the three tasks, with its condition, model and task version."""
    entries = []
    for path in sorted(runs_root.glob("**/*.run.json")):
        with open(path) as fh:
            data = json.load(fh)
        cond = run_condition(path, data, runs_root)
        if cond is None:
            continue
        model, version = run_layout(path, runs_root)
        if model is None:
            # The stored slug carries the provider ("google/gemini-2.5-flash").
            model = data.get("modelVersion", {}).get("slug", "unknown").split("/")[-1]
        entries.append({"path": path, "data": data, "condition": cond, "model": model,
                        "version": version, "end": end_time(data)})
    return entries


def choose_runs(entries: list[dict], cases: list[dict], truth: dict) -> dict:
    """{(condition, model): run} after rescoring every run.

    A complete run (a reply for every item) beats an incomplete one; among
    complete runs the newest task version wins, then the latest run. Among
    incomplete runs the one with the most replies is kept, for the record
    only: it is never ranked.
    """
    chosen: dict = {}
    for entry in entries:
        entry["rows"], entry["meta"] = score_run(entry, cases, truth)
        answered = sum(not r["errored"] for r in entry["rows"])
        entry["answered"] = answered
        entry["complete"] = answered == len(entry["rows"])
        entry["rank"] = (entry["complete"], entry["version"] if entry["complete"] else 0, answered, entry["end"])
        key = (entry["condition"], entry["model"])
        if key not in chosen or entry["rank"] > chosen[key]["rank"]:
            chosen[key] = entry
    return chosen


def score_run(entry: dict, cases: list[dict], truth: dict) -> tuple[list[dict], dict]:
    """Rescore every item of one run from the raw replies."""
    cond = entry["condition"]
    items = build_items(cases, truth, cond)
    index = by_id(cases)
    rows = []
    mismatches = 0
    stale = 0
    found = set()
    for sub in entry["data"].get("subruns", []):
        stored = dict_result(sub)
        if not stored or stored.get("item") not in items:
            continue
        item_id = stored["item"]
        item = items[item_id]
        users = role_texts(sub, "CONTENT_ROLE_USER")
        if not users or users[0] != item["prompt"]:
            stale += 1  # the run asked an older prompt; it cannot be scored against today's truth
            continue
        found.add(item_id)
        texts = assistant_texts(sub)
        parsed = parse_response(texts[0], unit=item["unit"]) if texts else None
        if parsed is None and len(texts) > 1:
            parsed = parse_response(texts[1], unit=item["unit"])
        case = index[item["case_id"]]
        row = score_response(case, truth[case["id"]], cond, item["version"], parsed)
        row.update(item=item_id, model=entry["model"], errored=False,
                   reasoning=(parsed or {}).get("reasoning"), raw=texts[-1] if texts else "")
        if abs(row["case_score"] - float(stored.get("case_score", -1))) > 1e-9:
            mismatches += 1
        rows.append(row)
    refused = sum("available quota" in sub.get("errorMessage", "") for sub in entry["data"].get("subruns", []))
    used = [usage(sub) for sub in entry["data"].get("subruns", [])]
    for item_id in sorted(set(items) - found):
        item = items[item_id]
        case = index[item["case_id"]]
        row = score_response(case, truth[case["id"]], cond, item["version"], None)
        # No reply at all: scored 0 like the Kaggle task does, but not a parse failure.
        row.update(item=item_id, model=entry["model"], errored=True, parse_failure=False, reasoning=None, raw="")
        rows.append(row)
    meta = {
        "file": entry["path"],
        "stored_score": task_score(entry["data"]),
        "state": entry["data"].get("state", ""),
        "errored_items": len(set(items) - found),
        "quota_refused": refused,
        "output_tokens": sum(u["tokens"] for u in used),
        "max_output_tokens": max((u["longest"] for u in used), default=0),
        "at_cap": sum(u["longest"] >= OUTPUT_CAP for u in used),
        "cost_usd": sum(u["cost"] for u in used),
        "stale_items": stale,
        "mismatches": mismatches,
    }
    return rows, meta


# ------------------------------------------------------------ computations
def pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def fmt(x, digits=2) -> str:
    if x is None:
        return "n/a"
    return f"{x:.{digits}f}"


def leaderboard(rows_by: dict, models: list[str]) -> list[dict]:
    """One line per model; the mean needs all three conditions. Models with
    all three come first, by mean."""
    out = []
    for m in models:
        line = {"model": m}
        scores = []
        pf = 0
        for cond in CONDITIONS:
            rows = rows_by.get((cond, m))
            if rows:
                s = summarize(rows)
                line[cond] = s["score"]
                scores.append(s["score"])
                pf += s["parse_failures"]
            else:
                line[cond] = None
        line["mean"] = sum(scores) / len(scores) if len(scores) == len(CONDITIONS) else None
        line["parse_failures"] = pf
        out.append(line)
    out.sort(key=lambda r: (r["mean"] is None, -(r["mean"] or 0), r["model"]))
    return out


def stability(entries: list[dict], chosen: dict) -> list[dict]:
    """Per model, how many reply pairs agree: each reply of the scored run
    against the reply to the same prompt in every other run of that model and
    condition."""
    out: dict = {}
    for entry in entries:
        key = (entry["condition"], entry["model"])
        main = chosen.get(key)
        if main is None or main is entry or not main["complete"]:
            continue
        base = {r["item"]: r for r in main["rows"]}
        line = out.setdefault(entry["model"], {"model": entry["model"], "runs": 0, "items": 0,
                                               "same_answer": 0, "same_mechanism": 0, "same_score": 0})
        before = line["items"]
        for r in entry["rows"]:
            b = base.get(r["item"])
            if r["errored"] or r["parse_failure"] or b is None or b["parse_failure"]:
                continue
            line["items"] += 1
            line["same_answer"] += int(r["answer"] == b["answer"])
            line["same_mechanism"] += int(r["mechanism"] == b["mechanism"])
            line["same_score"] += int(abs(r["case_score"] - b["case_score"]) < 1e-9)
        line["runs"] += int(line["items"] > before)
    return [line for line in out.values() if line["items"]]


def version_sensitivity(rows_by: dict, models: list[str], cases: list[dict], truth: dict) -> list[dict]:
    out = []
    for case in active_cases(cases):
        if not truth[case["id"]]["version_split"]:
            continue
        old, new = case["versions"][0], case["versions"][-1]
        for m in models:
            rows = {r["item"]: r for r in rows_by.get(("versioned", m), [])}
            a, b = rows.get(f"{case['id']}@{old}"), rows.get(f"{case['id']}@{new}")
            if not a or not b or a["errored"] or b["errored"]:
                continue
            both = a["answer_ok"] and b["answer_ok"]
            changed = a["answer"] != b["answer"]
            out.append({
                "case": case["id"], "model": m, "old": old, "new": new,
                "old_answer": a["answer"], "new_answer": b["answer"],
                "old_truth": a["expected_answer"], "new_truth": b["expected_answer"],
                "changed": changed, "tracks": bool(both),
            })
    return out


def evidence_update(rows_by: dict, models: list[str], cases: list[dict]) -> list[dict]:
    out = []
    for m in models:
        cold = {r["case"]: r for r in rows_by.get(("cold", m), []) if not r["errored"]}
        ver = defaultdict(list)
        evi = defaultdict(list)
        for r in rows_by.get(("versioned", m), []):
            ver[r["case"]].append(r)
        for r in rows_by.get(("evidence", m), []):
            evi[r["case"]].append(r)
        errored_cases = {r["case"] for rows in (ver.values(), evi.values()) for rs in rows for r in rs if r["errored"]}
        for cid in errored_cases:
            ver.pop(cid, None)
            evi.pop(cid, None)
        if not cold or not evi:
            continue
        gained = lost = 0
        v_gained = v_lost = 0
        for case in active_cases(cases):
            cid = case["id"]
            if cid not in cold or cid not in evi:
                continue
            e_ok = all(r["answer_ok"] for r in evi[cid])
            if not cold[cid]["answer_ok"] and e_ok:
                gained += 1
            if cold[cid]["answer_ok"] and not e_ok:
                lost += 1
            if ver.get(cid):
                v_ok = all(r["answer_ok"] for r in ver[cid])
                v_gained += int(not v_ok and e_ok)
                v_lost += int(v_ok and not e_ok)
        if not ver:  # no scored versioned run: nothing to compare with, which is not the same as zero
            v_gained = v_lost = None
        out.append({"model": m, "cold_to_evidence_gained": gained, "cold_to_evidence_lost": lost,
                    "versioned_to_evidence_gained": v_gained, "versioned_to_evidence_lost": v_lost,
                    "net": gained - lost})
    return out


# ------------------------------------------------------------------ charts
def _style(ax, plt):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(INK_2)
    ax.tick_params(colors=INK_2, labelsize=11)
    ax.yaxis.label.set_color(INK_2)
    ax.xaxis.label.set_color(INK_2)
    ax.title.set_color(INK)


def _figure(plt, w, h):
    fig = plt.figure(figsize=(w, h), dpi=160, facecolor=SURFACE)
    return fig


def chart_split_cases(cases: list[dict], truth: dict, out: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    split = [c for c in active_cases(cases)
             if truth[c["id"]]["version_split"] and list(c["decision"]["quantities"]) == ["r"]]
    fig = _figure(plt, 10, 3.8)
    axes = fig.subplots(1, len(split), squeeze=False)[0]
    labels = {
        "P1": "P1  reverse-built array / range()",
        "P4": "P4  SplFixedArray / plain list",
        "G3": "G3  map[int64]struct{} / map[int64]bool",
    }
    for ax, case in zip(axes, split):
        t = truth[case["id"]]
        qname = "r"
        versions = sorted(t["versions"], key=lambda v: tuple(int(x) for x in v.split("-")[1].split(".")))
        vals = [t["versions"][v]["quantities"][qname] for v in versions]
        xs = range(len(versions))
        ax.bar(xs, vals, color=SERIES[0], width=0.6, zorder=2)
        ax.axhline(1.0, color=INK_2, linewidth=1, linestyle=(0, (3, 3)), zorder=3)
        for x, v in zip(xs, vals):
            ax.text(x, v * 0.5, f"{v:.2f}x", ha="center", va="center", fontsize=10, color=SURFACE, zorder=4)
        ax.set_xticks(list(xs), [version_label(v) for v in versions], fontsize=10)
        ax.set_xlim(-0.5, len(versions) - 0.5)
        ax.set_ylim(0, max(max(vals), 1.0) * 1.12)
        ax.set_title(labels.get(case["id"], case["id"]), fontsize=11, loc="left")
        ax.set_yticks([])
        _style(ax, plt)
    fig.suptitle("Same code, different answer by runtime version (measured memory ratio)", fontsize=13, color=INK, x=0.01, ha="left")
    fig.text(0.01, 0.885, "Dashed line: 1x, both structures the same size. Medians of three readings per version.",
             fontsize=10, color=INK_2, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    path = out / "split_cases.png"
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def chart_leaderboard(board: list[dict], out: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    models = [r["model"] for r in board][::-1]
    fig = _figure(plt, 10, 0.55 * len(models) + 1.6)
    ax = fig.subplots()
    h = 0.26
    for i, cond in enumerate(CONDITIONS):
        vals = [next(r[cond] for r in board if r["model"] == m) or 0 for m in models]
        ys = [y + (1 - i) * h for y in range(len(models))]
        ax.barh(ys, vals, height=h - 0.04, color=SERIES[i], label=cond, zorder=2)
    ax.set_yticks(range(len(models)), models, fontsize=10)
    ax.set_xlim(0, 1)
    ax.set_xlabel("task score (mean of answer, estimate, mechanism)")
    ax.grid(axis="x", color=GRID, zorder=0)
    ax.legend(frameon=False, loc="lower right", fontsize=10, labelcolor=INK_2)
    ax.set_title("Score by model and condition", fontsize=13, loc="left")
    _style(ax, plt)
    fig.tight_layout()
    path = out / "leaderboard.png"
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def chart_heatmap(rows_by: dict, models: list[str], cases: list[dict], out: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap

    ids = [c["id"] for c in active_cases(cases)]
    grid = []
    for m in models:
        rows = {r["case"]: r["case_score"] for r in rows_by.get(("cold", m), [])}
        grid.append([rows.get(i, float("nan")) for i in ids])
    fig = _figure(plt, 10, 0.45 * len(models) + 1.8)
    ax = fig.subplots()
    cmap = LinearSegmentedColormap.from_list("seq", SEQ)
    im = ax.imshow(grid, cmap=cmap, vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(ids)), [i + ("*" if by_id(cases)[i]["control"] else "") for i in ids], fontsize=10)
    ax.set_yticks(range(len(models)), models, fontsize=10)
    for y, row in enumerate(grid):
        for x, v in enumerate(row):
            if v == v:
                label = {0: "0", 1: "1/3", 2: "2/3", 3: "1"}.get(round(v * 3), f"{v:.2f}")
                ax.text(x, y, label, ha="center", va="center", fontsize=9,
                        color=SURFACE if v > 0.6 else INK)
    cb = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.01)
    cb.ax.tick_params(labelsize=9, colors=INK_2)
    ax.set_title("Cold condition, case score per model (* = control)", fontsize=13, loc="left")
    _style(ax, plt)
    ax.spines["bottom"].set_visible(False)
    fig.tight_layout()
    path = out / "heatmap_cold.png"
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def chart_mechanism(rows_all: dict, models: list[str], out: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = ["right answer, right cause", "right answer, wrong cause", "wrong answer"]
    colors = [SERIES[0], SERIES[1], "#d6d5d0"]
    data = []
    for m in models:
        rows = rows_all[m]
        n = len(rows) or 1
        both = sum(r["answer_ok"] and r["mechanism_ok"] for r in rows) / n
        rawm = sum(r["right_answer_wrong_mechanism"] for r in rows) / n
        data.append([both, rawm, 1 - both - rawm])
    fig = _figure(plt, 10, 0.5 * len(models) + 1.6)
    ax = fig.subplots()
    left = [0.0] * len(models)
    ys = range(len(models))
    for i, lab in enumerate(labels):
        vals = [d[i] for d in data]
        ax.barh(list(ys), vals, left=left, color=colors[i], height=0.62, label=lab,
                edgecolor=SURFACE, linewidth=2, zorder=2)
        left = [a + b for a, b in zip(left, vals)]
    ax.set_yticks(list(ys), models, fontsize=10)
    ax.invert_yaxis()
    ax.set_xlim(0, 1)
    ax.set_xlabel("share of the items in the scored runs")
    ax.legend(frameon=False, ncol=3, loc="lower left", bbox_to_anchor=(0, 1.0), fontsize=10, labelcolor=INK_2)
    ax.set_title("Right answer for the wrong reason", fontsize=13, loc="left", pad=28)
    _style(ax, plt)
    fig.tight_layout()
    path = out / "mechanism.png"
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def chart_confidence(rows_all: dict, models: list[str], out: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = _figure(plt, 10, 0.5 * len(models) + 1.6)
    ax = fig.subplots()
    for y, m in enumerate(models):
        s = summarize(rows_all[m])
        right, wrong = s["mean_confidence_right"], s["mean_confidence_wrong"]
        pts = [(v, c, lab) for v, c, lab in ((right, SERIES[0], "when right"), (wrong, SERIES[1], "when wrong")) if v is not None]
        if len(pts) == 2:
            ax.plot([pts[0][0], pts[1][0]], [y, y], color=GRID, linewidth=2, zorder=1)
        for v, c, lab in pts:
            ax.scatter([v], [y], s=64, color=c, edgecolor=SURFACE, linewidth=2, zorder=2)
    ax.set_yticks(range(len(models)), models, fontsize=10)
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("mean stated confidence (0-100)")
    ax.grid(axis="x", color=GRID, zorder=0)
    from matplotlib.lines import Line2D

    handles = [Line2D([], [], marker="o", linestyle="", markersize=8, color=SERIES[0], label="when right"),
               Line2D([], [], marker="o", linestyle="", markersize=8, color=SERIES[1], label="when wrong")]
    ax.legend(handles=handles, frameon=False, ncol=2, loc="lower left", bbox_to_anchor=(0, 1.0), fontsize=10, labelcolor=INK_2)
    ax.set_title("Stated confidence when right and when wrong", fontsize=13, loc="left", pad=28)
    _style(ax, plt)
    fig.tight_layout()
    path = out / "confidence.png"
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


# ------------------------------------------------------------------ report
def table(header: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def measurement_summary(cases: list[dict], truth: dict) -> str:
    """One paragraph on how the truth was measured, read from truth.json."""
    entries = [e for c in active_cases(cases) for v, e in truth[c["id"]]["versions"].items()]
    readings = sorted({e["readings"] for e in entries})
    sources = sorted({e["source"] for e in entries})
    platforms = sorted({p for e in entries for p in e["platform"]})
    metrics = sorted({m for e in entries for m in e["metrics"].values()})
    versions = sorted({v for c in active_cases(cases) for v in truth[c["id"]]["versions"]})
    return (
        f"Readings per version: {', '.join(map(str, readings))} (median used). Source: {', '.join(sources)} "
        f"(gha = GitHub Actions). Platforms: {'; '.join(platforms)}. Versions measured: "
        f"{', '.join(version_label(v) for v in versions)}. Metrics: {', '.join(metrics)}, as deltas around "
        "the measured structure; Go readings follow `runtime.GC()`, PHP readings follow `gc_collect_cycles()`. "
        "Full values per version are in `cases/truth.json`. The decision quantities are the ratios each "
        "case's rules in `cases/cases.json` use (for example r = b / a)."
    )


def truth_section(cases: list[dict], truth: dict) -> str:
    rows = []
    for case in active_cases(cases):
        t = truth[case["id"]]
        per = []
        for v in case["versions"]:
            e = t["versions"][v]
            q = ", ".join(f"{k} = {x:.2f}" for k, x in e["quantities"].items())
            per.append(f"{version_label(v)}: {e['answer']}. {case['options'][e['answer']]} ({q}; {e['estimate']} {e['estimate_unit']})")
        rows.append([
            case["id"] + (" (control)" if case["control"] else ""),
            case["title"],
            "<br>".join(per).replace("|", "/"),
            f"{t['cold_answer']}. {case['options'][t['cold_answer']]}".replace("|", "/"),
            f"{case['folklore_answer']}. {case['options'][case['folklore_answer']]}".replace("|", "/"),
        ])
    return table(["case", "what is measured", "measured answer per version asked (decision quantities; estimate target)", "cold answer", "rule of thumb"], rows)


# Two cases with the same idle goroutines and a different history before them.
PAIR = ("R4", "R6")


def pair_counts(rows_by: dict) -> tuple[dict, list[str]]:
    """Per condition, how many scored runs got both cases of PAIR right, one
    of them, or neither; and the runs that got both."""
    counts = {cond: Counter() for cond in CONDITIONS}
    both = []
    for (cond, model), rows in sorted(rows_by.items()):
        ok = {r["case"]: bool(r["answer_ok"]) for r in rows if r["case"] in PAIR}
        if len(ok) < 2:
            continue
        key = (ok[PAIR[0]], ok[PAIR[1]])
        counts[cond][key] += 1
        if key == (True, True):
            both.append(f"`{model}` ({cond})")
    return counts, both


def control_quote_rows(rows_all: dict, order: list[str], cases: list[dict], per_case: int = 3) -> list[list]:
    """For each control, the shortest reasoning of the best-ranked models that answered it wrong."""
    out = []
    for c in active_cases(cases):
        if not c["control"]:
            continue
        shown = 0
        for m in order:
            wrong = [r for r in rows_all.get(m, []) if r["case"] == c["id"] and not r["answer_ok"] and r.get("reasoning")]
            if not wrong or shown >= per_case:
                continue
            r = min(wrong, key=lambda x: len(x["reasoning"]))
            picked = f"{r['answer']}. {c['options'][r['answer']]}" if r["answer"] in c["options"] else "-"
            out.append([c["id"], f"`{m}`", r["condition"], picked.replace("|", "/"), r["confidence"],
                        '"' + " ".join(r["reasoning"].split()).replace("|", "/") + '"'])
            shown += 1
    return out


def quote_rows(rows_all: dict, cases: list[dict]) -> list[list]:
    index = by_id(cases)
    best: dict = {}
    for m, rows in rows_all.items():
        for r in rows:
            if not r["folklore_mechanism"] or not r.get("reasoning"):
                continue
            cur = best.get(r["case"])
            if cur is None or len(r["reasoning"]) < len(cur[1]):
                best[r["case"]] = (m, r["reasoning"], r["condition"])
    out = []
    for c in active_cases(cases):
        if c["id"] in best:
            m, text, cond = best[c["id"]]
            out.append([c["id"], f"`{m}`", cond, '"' + " ".join(text.split()).replace("|", "/") + '"'])
    return out


def build_report(cases, truth, entries, runs_root: Path, out_dir: Path, banner: str | None) -> str:
    charts = out_dir / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    rel = lambda p: p.relative_to(out_dir).as_posix()  # noqa: E731
    runs_name = runs_root.relative_to(ROOT).as_posix() if runs_root.is_relative_to(ROOT) else runs_root.name
    parts = ["# MemoryTrap Bench: results", ""]
    if banner:
        parts += [f'<p style="color:#c0392b"><b>{banner}</b></p>', ""]
    parts += [
        "Generated by `uv run python -m memtrap.analyze` from `cases/truth.json` and the run files "
        f"under `{runs_name}/`. "
        "Sizes are binary (1 MB = 1,048,576 bytes). Every model reply is parsed and scored again from its raw text.",
        "",
        "## 1. What the harness measured",
        "",
        measurement_summary(cases, truth),
        "",
        truth_section(cases, truth),
        "",
        f"![measured ratios by version]({rel(chart_split_cases(cases, truth, charts))})",
        "",
        "Prompts per task: " + ", ".join(f"{cond} {len(build_items(cases, truth, cond))}" for cond in CONDITIONS)
        + ". Cases whose answer differs between the two versions asked are asked once per version in the "
        "versioned and evidence tasks.",
        "",
    ]

    chosen = choose_runs(entries, cases, truth)
    if not chosen:
        parts += [
            "## 2. Model results",
            "",
            f"Pending: there are no Kaggle run files under `{runs_name}/` yet. "
            "Run `sh kaggle/run.sh all`, then `uv run python -m memtrap.analyze`.",
            "",
        ]
        return "\n".join(parts)

    # Only complete runs are scored; the rest are listed and left out of every table.
    rows_by = {key: e["rows"] for key, e in chosen.items() if e["complete"]}
    metas = {key: e["meta"] for key, e in chosen.items() if e["complete"]}
    incomplete = {key: e for key, e in chosen.items() if not e["complete"]}
    models = sorted({m for _, m in rows_by})
    rows_all = {m: [r for (c, mm), rows in rows_by.items() if mm == m for r in rows] for m in models}
    board = leaderboard(rows_by, models)
    order = [r["model"] for r in board]
    full = [r for r in board if r["mean"] is not None]

    parts += ["## 2. Leaderboard", "",
              "Task score = mean over items of (answer + estimate + mechanism) / 3. A model is scored on a "
              "condition only from a run in which every item got a reply. The mean needs all three conditions; "
              "models without it are listed below the ranked ones.",
              "",
              table(["model", "cold", "versioned", "evidence", "mean", "parse failures"],
                    [[f"`{r['model']}`", fmt(r["cold"]), fmt(r["versioned"]), fmt(r["evidence"]), fmt(r["mean"]),
                      r["parse_failures"]] for r in board]),
              ""]
    if full:
        parts += [f"![leaderboard]({rel(chart_leaderboard(full, charts))})", ""]
    else:
        (charts / "leaderboard.png").unlink(missing_ok=True)
    mism = sum(m["mismatches"] for m in metas.values())
    stale = sum(e["meta"]["stale_items"] for e in entries)
    versions = sorted({e["version"] for e in chosen.values() if e["complete"]})
    stored_diff = [
        f"`{model}` {cond}: stored {fmt(meta['stored_score'], 3)}, rescored {fmt(summarize(rows_by[(cond, model)])['score'], 3)}"
        for (cond, model), meta in sorted(metas.items())
        if abs(meta["stored_score"] - summarize(rows_by[(cond, model)])["score"]) > 1e-6
    ]
    version_note = f" (task version {', '.join(map(str, versions))})" if any(versions) else ""
    parts += [f"Run files of the three tasks: {len(entries)}. Scored: {len(rows_by)} complete runs"
              f"{version_note}, one per model and condition. "
              f"Items whose prompt differs from the current task (stale, never scored): {stale}. "
              f"Items whose stored score differs from the rescore: {mism}.", ""]
    if stored_diff:
        parts += ["Task scores that differ from the stored Kaggle score: " + "; ".join(stored_diff) + ".", ""]

    refused = sum(e["meta"]["quota_refused"] for e in entries)
    no_reply = sum(e["meta"]["errored_items"] for e in entries)
    parts += ["### Runs without a reply for every item", "",
              f"{no_reply} items in {sum(1 for e in entries if not e['complete'])} of the {len(entries)} run files "
              f"got no reply; {refused} of them were refused by Kaggle's model proxy with \"The max estimated cost "
              "of operation exceeds your available quota\". The proxy reserves the worst-case cost of every call "
              "(the model's maximum output at its output price) against the daily quota while the call runs, so "
              "many models at once are refused long before the money is spent. Those runs are kept in the "
              "repository; their replies are compared with the scored runs in section 12 and used nowhere else.", ""]
    if incomplete:
        inc_rows = []
        for (cond, model), e in sorted(incomplete.items(), key=lambda kv: (kv[0][1], CONDITIONS.index(kv[0][0]))):
            inc_rows.append([f"`{model}`", cond, e["version"] or "n/a", f"{e['answered']} of {len(e['rows'])}",
                             e["meta"]["quota_refused"]])
        parts += ["Model and condition pairs with no complete run yet. They are not ranked and not used in the "
                  "sections below; a score over the items that happened to get through would not be comparable.", "",
                  table(["model", "condition", "task version", "items answered", "refused on quota"], inc_rows), ""]
    else:
        parts += ["Every model and condition pair has a complete run.", ""]

    cost_rows = []
    for m in order:
        ms = [meta for (c, mm), meta in metas.items() if mm == m]
        items = len(rows_all[m])
        cost_rows.append([f"`{m}`", items, f"{sum(x['output_tokens'] for x in ms) / items:,.0f}",
                          f"{max(x['max_output_tokens'] for x in ms):,}", f"{sum(x['cost_usd'] for x in ms):.3f}"])
    parts += ["### Output and cost of the scored runs", "",
              "Output tokens include the model's reasoning tokens. Cost is what the proxy charged, from the run files.", "",
              table(["model", "items", "output tokens per item", "longest reply", "cost, USD"], cost_rows), "",
              f"The run files of the three tasks together cost {sum(e['meta']['cost_usd'] for e in entries):.2f} USD. "
              f"The longest single reply in any of them used {max(e['meta']['max_output_tokens'] for e in entries):,} "
              f"output tokens; replies that reached the {OUTPUT_CAP:,}-token cap of task version 2: "
              f"{sum(e['meta']['at_cap'] for e in entries if e['version'] >= 2)}.", ""]

    cold_order = [m for m in order if ("cold", m) in rows_by]
    parts += ["## 3. Per case, cold condition", ""]
    if cold_order:
        parts += [f"![heatmap]({rel(chart_heatmap(rows_by, cold_order, cases, charts))})", ""]
    else:
        (charts / "heatmap_cold.png").unlink(missing_ok=True)
        parts += ["No complete cold run yet.", ""]

    mech_rows = []
    for m in order:
        rows = rows_all[m]
        right = sum(r["answer_ok"] for r in rows)
        rawm = sum(r["right_answer_wrong_mechanism"] for r in rows)
        mech_rows.append([f"`{m}`", len(rows), right, rawm, pct(rawm / right) if right else "n/a",
                          sum(r["folklore_mechanism"] for r in rows)])
    parts += ["## 4. Right answer, wrong mechanism", "",
              "All items of the conditions a model completed. Folklore mechanism counts only non-control items.", "",
              table(["model", "items", "right answers", "right answer, wrong mechanism", "share of right answers", "folklore mechanism chosen"], mech_rows),
              "", f"![mechanism]({rel(chart_mechanism(rows_all, order, charts))})", ""]

    conf_rows = []
    for m in order:
        s = summarize(rows_all[m])
        gap = None
        if s["mean_confidence_right"] is not None and s["mean_confidence_wrong"] is not None:
            gap = s["mean_confidence_right"] - s["mean_confidence_wrong"]
        conf_rows.append([f"`{m}`", fmt(s["mean_confidence_right"], 1), fmt(s["mean_confidence_wrong"], 1), fmt(gap, 1),
                          len(rows_all[m]) - s["answer_ok"], s["confidently_wrong"]])
    parts += ["## 5. Confidence", "",
              "Confidently wrong = wrong answer with stated confidence of 80 or more.", "",
              table(["model", "mean confidence when right", "when wrong", "calibration gap", "wrong answers", "confidently wrong"], conf_rows),
              "", f"![confidence]({rel(chart_confidence(rows_all, order, charts))})", ""]

    vs = version_sensitivity(rows_by, order, cases, truth)
    parts += ["## 6. Version sensitivity", "",
              "Versioned condition, the cases whose measured answer differs between the two versions asked. "
              "Tracks = both version answers correct.", ""]
    if vs:
        parts += [table(["case", "model", "older version: answer (measured)", "newer version: answer (measured)", "changed", "tracks"],
                        [[v["case"], f"`{v['model']}`", f"{version_label(v['old'])}: {v['old_answer'] or '-'} ({v['old_truth']})",
                          f"{version_label(v['new'])}: {v['new_answer'] or '-'} ({v['new_truth']})",
                          "yes" if v["changed"] else "no", "yes" if v["tracks"] else "no"] for v in vs]), ""]
        per_model = Counter(v["model"] for v in vs if v["tracks"])
        split_n = len({v["case"] for v in vs})
        with_versioned = [m for m in order if ("versioned", m) in rows_by]
        parts += [f"Per model, split cases tracked (of {split_n}): "
                  + ", ".join(f"`{m}` {per_model.get(m, 0)}" for m in with_versioned) + ".", ""]
        per_case = Counter(v["case"] for v in vs if v["tracks"])
        parts += ["Per case, models that tracked it: "
                  + ", ".join(f"{c} {per_case.get(c, 0)} of {sum(1 for v in vs if v['case'] == c)}"
                              for c in sorted({v["case"] for v in vs})) + ".", ""]
    else:
        parts += ["No complete versioned run yet.", ""]

    eu = evidence_update(rows_by, order, cases)
    parts += ["## 7. Evidence update", "",
              "Per case, answer correctness moving from cold (or versioned) to evidence. Net = gained minus lost.", ""]
    if eu:
        parts += [table(["model", "cold to evidence: gained", "lost", "net", "versioned to evidence: gained", "lost"],
                        [[f"`{e['model']}`", e["cold_to_evidence_gained"], e["cold_to_evidence_lost"], e["net"],
                          "n/a" if e["versioned_to_evidence_gained"] is None else e["versioned_to_evidence_gained"],
                          "n/a" if e["versioned_to_evidence_lost"] is None else e["versioned_to_evidence_lost"]]
                         for e in eu]), ""]
    else:
        parts += ["No complete evidence run yet.", ""]

    folk = []
    for c in active_cases(cases):
        if c["control"]:
            folk.append([c["id"], "control", "n/a", "n/a"])
            continue
        rs = [r for rows in rows_all.values() for r in rows if r["case"] == c["id"] and not r["errored"]]
        folk.append([c["id"], len(rs), sum(r["folklore_mechanism"] for r in rs), sum(r["folklore_answer"] for r in rs)])
    parts += ["## 8. Folklore index", "",
              "How often each case's folklore mechanism and folklore answer were chosen, over every scored run. "
              "Controls are never counted.", "",
              table(["case", "replies", "folklore mechanism", "folklore answer (when wrong)"], folk), ""]

    ctrl = [c["id"] for c in active_cases(cases) if c["control"]]
    ctrl_rows = []
    for m in order:
        asked = [r for r in rows_all[m] if r["case"] in ctrl]
        fails = [f"{r['item']} ({r['condition']})" for r in asked if not r["answer_ok"]]
        ctrl_rows.append([f"`{m}`", len(asked), len(fails), ", ".join(fails) or "-"])
    parts += ["## 9. Controls", "",
              f"Controls ({', '.join(ctrl)}) are cases where the rule of thumb holds. A model that bets against every rule loses them.", "",
              table(["model", "control items asked", "control answers wrong", "items"], ctrl_rows), ""]

    index = by_id(cases)
    if all(cid in index and cid in truth for cid in PAIR):
        counts, both = pair_counts(rows_by)
        measured = {cid: index[cid]["options"][truth[cid]["cold_answer"]] for cid in PAIR}
        total = sum(sum(c.values()) for c in counts.values())
        parts += [f"### {PAIR[0]} and {PAIR[1]}: the same goroutines, a different history", "",
                  f"Both cases start the same idle goroutines after 100,000 goroutines with 16 KB stacks. In {PAIR[0]} "
                  f"(a control) the big ones are still alive and the harness measured: {measured[PAIR[0]]}. In {PAIR[1]} "
                  f"they have exited and it measured: {measured[PAIR[1]]}. One row per condition, one scored run per model.", "",
                  table(["condition", "runs", "both right", f"only {PAIR[0]} right", f"only {PAIR[1]} right", "neither"],
                        [[cond, sum(counts[cond].values()), counts[cond][(True, True)], counts[cond][(True, False)],
                          counts[cond][(False, True)], counts[cond][(False, False)]] for cond in CONDITIONS]), "",
                  f"Both right in {len(both)} of {total} runs" + (": " + ", ".join(both) if both else "") + ".", ""]

    parts += ["## 10. Human baseline", "",
              "One developer answered the cold condition before seeing any measurement. The score is computed by "
              "`uv run python -m memtrap.score_human human/answers.json` and is not read by this report.", ""]

    q = quote_rows(rows_all, cases)
    parts += ["## 11. Folklore in the models' own words", "",
              "For each case, the shortest reasoning among replies that chose the folklore mechanism, verbatim.", ""]
    parts += [table(["case", "model", "condition", "reasoning"], q) if q else "No reply chose a folklore mechanism with a reasoning.", ""]
    cq = control_quote_rows(rows_all, order, cases)
    if cq:
        parts += ["### A control lost, in the models' own words", "",
                  "For each control, the best-ranked models that answered it wrong (at most three), each with its "
                  "shortest reasoning, verbatim.", "",
                  table(["case", "model", "condition", "answer chosen", "confidence", "reasoning"], cq), ""]

    st = stability(entries, chosen)
    parts += ["## 12. The same question twice", "",
              "Some models have more than one run of the same prompts: the scored run and earlier runs, most of "
              "them with quota gaps. Each reply of an earlier run is paired with the scored run's reply to the "
              "same prompt. The runs differ in two ways: no temperature is set, and the earlier runs of task "
              "version 1 had no `max_tokens` cap.", ""]
    if st:
        st.sort(key=lambda r: order.index(r["model"]) if r["model"] in order else len(order))
        tot = {k: sum(r[k] for r in st) for k in ("items", "same_answer", "same_mechanism", "same_score")}
        parts += [table(["model", "other runs", "reply pairs", "same answer", "same mechanism", "same item score"],
                        [[f"`{r['model']}`", r["runs"], r["items"], f"{r['same_answer']} ({pct(r['same_answer'] / r['items'])})",
                          f"{r['same_mechanism']} ({pct(r['same_mechanism'] / r['items'])})",
                          f"{r['same_score']} ({pct(r['same_score'] / r['items'])})"] for r in st]), "",
                  f"All models: {tot['items']} reply pairs; same answer in {tot['same_answer']} "
                  f"({pct(tot['same_answer'] / tot['items'])}), same mechanism in {tot['same_mechanism']} "
                  f"({pct(tot['same_mechanism'] / tot['items'])}), same item score in {tot['same_score']} "
                  f"({pct(tot['same_score'] / tot['items'])}).", ""]
    else:
        parts += ["No model has two runs of the same prompts yet.", ""]

    parts += ["## 13. Limits", "",
              f"- {len(active_cases(cases))} cases, {len(order)} models with at least one complete run, one scored run per model and condition.",
              "- kaggle-benchmarks 0.6.1 does not send a temperature through the Kaggle model proxy "
              "(`kaggle_benchmarks/kaggle/model_proxy.py` sets `support_temperature` to False), so a rerun can differ; "
              "section 12 shows by how much.",
              f"- From task version 2 on, the task sends `max_tokens={OUTPUT_CAP}` with every call, which lowers the "
              "proxy's quota reservation; a model whose provider rejects the parameter is called again without it, and "
              "the run files do not record which. The longest single reply in any run file used "
              f"{max(e['meta']['max_output_tokens'] for e in entries):,} output tokens.",
              "- Replies that cannot be parsed after one repair turn score zero and are counted as parse failures.",
              "- Measurements are from GitHub Actions runners (linux/amd64, PHP NTS CLI with opcache off); other builds and allocators can differ.",
              ""]
    return "\n".join(parts)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=Path, default=RESULTS / "kaggle")
    ap.add_argument("--out", type=Path, default=RESULTS)
    ap.add_argument("--banner", default=None)
    args = ap.parse_args(argv)
    cases, truth = load_cases(), load_truth()
    entries = load_runs(args.runs.resolve()) if args.runs.exists() else []
    report = build_report(cases, truth, entries, args.runs.resolve(), args.out.resolve(), args.banner)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "REPORT.md").write_text(report)
    print(f"wrote {args.out / 'REPORT.md'} ({len(entries)} run files, {len({e['model'] for e in entries})} models)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
