"""Read the Kaggle run files and the measured truth, write results/REPORT.md
and the charts in results/charts/.

    uv run python -m memtrap.analyze
    uv run python -m memtrap.analyze --runs DIR --out DIR --banner "STUB DATA"

Run files are found under <runs>/{cold,versioned,evidence}/**/*.run.json. Every
reply is parsed and scored again from the raw text with memtrap.parse and
memtrap.score, so the report does not depend on the scores stored in the run
files; a disagreement between the two is reported. The human quiz is never
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


def dict_result(run: dict) -> dict | None:
    for r in run.get("results", []):
        if "dictResult" in r:
            return r["dictResult"]
    return None


def load_runs(runs_root: Path) -> tuple[dict, list[dict]]:
    """{(condition, model): newest run} and a list of every run file seen."""
    newest: dict = {}
    seen = []
    for path in sorted(runs_root.glob("**/*.run.json")):
        with open(path) as fh:
            data = json.load(fh)
        cond = run_condition(path, data, runs_root)
        if cond is None:
            continue
        model = data.get("modelVersion", {}).get("slug", "unknown")
        entry = {"path": path, "data": data, "condition": cond, "model": model, "end": end_time(data)}
        seen.append(entry)
        key = (cond, model)
        if key not in newest or entry["end"] > newest[key]["end"]:
            newest[key] = entry
    return newest, seen


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
        line["mean"] = sum(scores) / len(scores) if scores else None
        line["parse_failures"] = pf
        line["errored"] = sum(r["errored"] for cond in CONDITIONS for r in rows_by.get((cond, m), []))
        out.append(line)
    out.sort(key=lambda r: (-(r["mean"] or 0), r["model"]))
    return out


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
    ax.set_xlabel("share of all items, three conditions")
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


def build_report(cases, truth, newest, seen, runs_root: Path, out_dir: Path, banner: str | None) -> str:
    charts = out_dir / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    rel = lambda p: p.relative_to(out_dir).as_posix()  # noqa: E731
    parts = ["# MemoryTrap Bench: results", ""]
    if banner:
        parts += [f'<p style="color:#c0392b"><b>{banner}</b></p>', ""]
    parts += [
        "Generated by `uv run python -m memtrap.analyze` from `cases/truth.json` and the run files "
        f"under `{runs_root.relative_to(ROOT).as_posix() if runs_root.is_relative_to(ROOT) else runs_root.name}/`. "
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

    rows_by: dict = {}
    metas = {}
    for (cond, model), entry in sorted(newest.items()):
        rows, meta = score_run(entry, cases, truth)
        rows_by[(cond, model)] = rows
        metas[(cond, model)] = meta
    models = sorted({m for _, m in rows_by})

    if not models:
        parts += [
            "## 2. Model results",
            "",
            "Pending: there are no Kaggle run files under "
            f"`{runs_root.relative_to(ROOT).as_posix() if runs_root.is_relative_to(ROOT) else runs_root}/` yet. "
            "Run `sh kaggle/run.sh all`, then `uv run python -m memtrap.analyze`.",
            "",
        ]
        return "\n".join(parts)

    rows_all = {m: [r for (c, mm), rows in rows_by.items() if mm == m for r in rows] for m in models}
    board = leaderboard(rows_by, models)
    order = [r["model"] for r in board]

    parts += ["## 2. Leaderboard", "",
              "Task score = mean over items of (answer + estimate + mechanism) / 3. The mean column averages the conditions a model completed.",
              "",
              table(["model", "cold", "versioned", "evidence", "mean", "parse failures", "no reply"],
                    [[f"`{r['model']}`", fmt(r["cold"]), fmt(r["versioned"]), fmt(r["evidence"]), fmt(r["mean"]),
                      r["parse_failures"], r["errored"]] for r in board]),
              "", f"![leaderboard]({rel(chart_leaderboard(board, charts))})", ""]
    mism = sum(m["mismatches"] for m in metas.values())
    errored = sum(m["errored_items"] for m in metas.values())
    stale = sum(m["stale_items"] for m in metas.values())
    stored_diff = [
        f"`{model}` {cond}: stored {fmt(meta['stored_score'], 3)}, rescored {fmt(summarize(rows_by[(cond, model)])['score'], 3)}"
        for (cond, model), meta in sorted(metas.items())
        if abs(meta["stored_score"] - summarize(rows_by[(cond, model)])["score"]) > 1e-6
    ]
    parts += [f"Run files used: {len(newest)} (newest per model and condition) of {len(seen)} found. "
              f"Items with no result in their run file (no reply, scored 0): {errored}. "
              f"Items whose prompt differs from the current task (stale, scored 0): {stale}. "
              f"Items whose stored score differs from the rescore: {mism}.", ""]
    if stored_diff:
        parts += ["Task scores that differ from the stored Kaggle score: " + "; ".join(stored_diff) + ".", ""]

    parts += ["## 3. Per case, cold condition", "",
              f"![heatmap]({rel(chart_heatmap(rows_by, order, cases, charts))})", ""]

    mech_rows = []
    for m in order:
        rows = rows_all[m]
        right = sum(r["answer_ok"] for r in rows)
        rawm = sum(r["right_answer_wrong_mechanism"] for r in rows)
        mech_rows.append([f"`{m}`", len(rows), right, rawm, pct(rawm / right) if right else "n/a",
                          sum(r["folklore_mechanism"] for r in rows)])
    parts += ["## 4. Right answer, wrong mechanism", "",
              "All items of all three conditions. Folklore mechanism counts only non-control items.", "",
              table(["model", "items", "right answers", "right answer, wrong mechanism", "share of right answers", "folklore mechanism chosen"], mech_rows),
              "", f"![mechanism]({rel(chart_mechanism(rows_all, order, charts))})", ""]

    conf_rows = []
    for m in order:
        s = summarize(rows_all[m])
        gap = None
        if s["mean_confidence_right"] is not None and s["mean_confidence_wrong"] is not None:
            gap = s["mean_confidence_right"] - s["mean_confidence_wrong"]
        conf_rows.append([f"`{m}`", fmt(s["mean_confidence_right"], 1), fmt(s["mean_confidence_wrong"], 1), fmt(gap, 1),
                          s["confidently_wrong"]])
    parts += ["## 5. Confidence", "",
              "Confidently wrong = wrong answer with stated confidence of 80 or more.", "",
              table(["model", "mean confidence when right", "when wrong", "calibration gap", "confidently wrong"], conf_rows),
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
        parts += ["Per model, split cases tracked: " + ", ".join(f"`{m}` {per_model.get(m, 0)}" for m in order) + ".", ""]
    else:
        parts += ["No versioned run files yet.", ""]

    eu = evidence_update(rows_by, order, cases)
    parts += ["## 7. Evidence update", "",
              "Per case, answer correctness moving from cold (or versioned) to evidence. Net = gained minus lost.", ""]
    if eu:
        parts += [table(["model", "cold to evidence: gained", "lost", "net", "versioned to evidence: gained", "lost"],
                        [[f"`{e['model']}`", e["cold_to_evidence_gained"], e["cold_to_evidence_lost"], e["net"],
                          e["versioned_to_evidence_gained"], e["versioned_to_evidence_lost"]] for e in eu]), ""]
    else:
        parts += ["No evidence run files yet.", ""]

    folk = []
    for c in active_cases(cases):
        if c["control"]:
            folk.append([c["id"], "control", "n/a", "n/a"])
            continue
        rs = [r for rows in rows_all.values() for r in rows if r["case"] == c["id"] and not r["errored"]]
        folk.append([c["id"], len(rs), sum(r["folklore_mechanism"] for r in rs), sum(r["folklore_answer"] for r in rs)])
    parts += ["## 8. Folklore index", "",
              "How often each case's folklore mechanism and folklore answer were chosen, all models and conditions. "
              "Controls are never counted.", "",
              table(["case", "replies", "folklore mechanism", "folklore answer (when wrong)"], folk), ""]

    ctrl = [c["id"] for c in active_cases(cases) if c["control"]]
    ctrl_rows = []
    for m in order:
        fails = [f"{r['item']} ({r['condition']})" for r in rows_all[m] if r["case"] in ctrl and not r["answer_ok"]]
        ctrl_rows.append([f"`{m}`", len(fails), ", ".join(fails) or "-"])
    parts += ["## 9. Controls", "",
              f"Controls ({', '.join(ctrl)}) are cases where the rule of thumb holds. A model that bets against every rule loses them.", "",
              table(["model", "control answers wrong", "items"], ctrl_rows), ""]

    parts += ["## 10. Human baseline", "",
              "One developer answered the cold condition before seeing any measurement. The score is computed by "
              "`uv run python -m memtrap.score_human human/answers.json` and is not read by this report.", ""]

    q = quote_rows(rows_all, cases)
    parts += ["## 11. Folklore in the models' own words", "",
              "For each case, the shortest reasoning among replies that chose the folklore mechanism, verbatim.", ""]
    parts += [table(["case", "model", "condition", "reasoning"], q) if q else "No reply chose a folklore mechanism with a reasoning.", ""]

    parts += ["## 12. Limits", "",
              f"- {len(active_cases(cases))} cases, {len(order)} models, one run per model and condition unless stated.",
              "- kaggle-benchmarks 0.6.1 does not send a temperature through the Kaggle model proxy "
              "(`kaggle_benchmarks/kaggle/model_proxy.py` sets `support_temperature` to False), so a rerun can differ.",
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
    newest, seen = load_runs(args.runs) if args.runs.exists() else ({}, [])
    report = build_report(cases, truth, newest, seen, args.runs.resolve(), args.out.resolve(), args.banner)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "REPORT.md").write_text(report)
    print(f"wrote {args.out / 'REPORT.md'} ({len(newest)} run files, {len({m for _, m in newest})} models)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
