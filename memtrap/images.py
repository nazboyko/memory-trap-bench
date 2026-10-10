"""Write the three images of the write-up into docs/img/.

    uv run python -m memtrap.images

    cover.png           1000 x 420   the title, the counts, said against measured for the control case
    leaderboard.png     1600 wide    mean task score per model, the three task scores as markers
    card-goroutine.png  1200 x 675   the control case: the code, what the two top models said, what was measured

Every number comes from the run files under results/kaggle/, cases/truth.json
and results/human_baseline.json. The cover and the card state one claim (the
two top models gave one wrong answer on the control case) and are refused
when the data stops supporting it. Matplotlib draws, Pillow checks the size
and writes the final file.
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, PathPatch, Rectangle  # noqa: E402
from matplotlib.path import Path as MplPath  # noqa: E402
from PIL import Image  # noqa: E402

from memtrap.analyze import PAIR, choose_runs, leaderboard, load_baseline, load_runs  # noqa: E402
from memtrap.catalog import ROOT, active_cases, by_id, load_cases, load_truth, version_label  # noqa: E402
from memtrap.prompts import CONDITIONS  # noqa: E402

OUT = ROOT / "docs" / "img"
RUNS = ROOT / "results" / "kaggle"
RESULTS = ROOT / "results"
TITLE = ("The Models Read the Release Notes.", "I Measured the Runtime.")

# Light and dark values of one palette: surface, ink, three categorical slots
# that stay apart for colour-blind readers, one light step for the mean bar.
LIGHT = {"surface": "#fcfcfb", "panel": "#f1f0ec", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
         "grid": "#e1e0d9", "axis": "#c3c2b7", "blue": "#2a78d6", "orange": "#eb6834", "aqua": "#1baf7a",
         "bar": "#86b6ef"}
DARK = {"surface": "#1a1a19", "ink": "#ffffff", "ink2": "#c3c2b7", "muted": "#898781", "grid": "#383835",
        "blue": "#3987e5", "orange": "#d95926"}
SANS = "DejaVu Sans"
MONO = "DejaVu Sans Mono"


class ImageError(Exception):
    pass


# --------------------------------------------------------------------- data
def collect(runs_root: Path = RUNS, results_dir: Path = RESULTS) -> dict:
    """Everything the three images show, from the scored runs and the truth."""
    cases, truth = load_cases(), load_truth()
    chosen = choose_runs(load_runs(runs_root), cases, truth)
    rows_by = {key: e["rows"] for key, e in chosen.items() if e["complete"]}
    board = [r for r in leaderboard(rows_by, sorted({m for _, m in rows_by})) if r["mean"] is not None]
    if len(board) < 2:
        raise ImageError("fewer than two models have a complete run on all three tasks")

    control = by_id(cases)[PAIR[0]]
    t = truth[control["id"]]
    said = []
    for line in board[:2]:
        rows = [r for cond in CONDITIONS for r in rows_by[(cond, line["model"])] if r["case"] == control["id"]]
        said.append({
            "model": line["model"],
            "answers": [r["answer"] for r in rows],
            "estimates": [r["estimate"] for r in rows],
            "confidences": [r["confidence"] for r in rows],
            "right": [bool(r["answer_ok"]) for r in rows],
            "estimate_right": [bool(r["estimate_ok"]) for r in rows],
        })
    versions = sorted(t["versions"], key=lambda v: tuple(int(x) for x in v.split("-")[1].split(".")))
    measured = [{"version": version_label(v), "value": t["versions"][v]["estimate"],
                 "unit": t["versions"][v]["estimate_unit"], "readings": t["versions"][v]["readings"],
                 "n": t["versions"][v]["n"]} for v in versions]
    active = active_cases(cases)
    return {
        "board": board,
        "control": {"id": control["id"], "metric": t["metric"], "said": said, "measured": measured,
                    "snippet": control["snippet_a"]},
        "baseline": load_baseline(results_dir),
        "cases": len(active),
        "models": len(board),
        "runtime_versions": len({v for c in active for v in truth[c["id"]]["versions"]}),
    }


def short_versions(labels: list[str]) -> str:
    """["Go 1.23", "Go 1.24"] -> "Go 1.23, 1.24" when every label names the same runtime."""
    names = {label.rsplit(" ", 1)[0] for label in labels}
    if len(names) != 1:
        return ", ".join(labels)
    return f"{names.pop()} " + ", ".join(label.rsplit(" ", 1)[1] for label in labels)


def control_pair(data: dict) -> dict:
    """The one wrong answer of the two top models and the one measured value.
    Refuses to make a card that the data no longer supports."""
    c, board = data["control"], data["board"]

    def refuse(why: str):
        raise ImageError(f"{c['id']}: {why}; rewrite the card and the cover")

    if len(board) > 2 and abs(board[1]["mean"] - board[2]["mean"]) < 1e-9:
        refuse("the second and third model are level, so there are no two top models")
    for m in c["said"]:
        if len(m["answers"]) != len(CONDITIONS):
            refuse(f"{m['model']} has {len(m['answers'])} replies, not one per task")
        if any(x is None for x in m["estimates"] + m["confidences"] + m["answers"]):
            refuse(f"{m['model']} left out an answer, an estimate or a confidence")
        if any(m["right"]) or any(m["estimate_right"]):
            refuse(f"{m['model']} now gets the answer or the estimate right")
    said = {e for m in c["said"] for e in m["estimates"]}
    if len(said) != 1 or len({a for m in c["said"] for a in m["answers"]}) != 1:
        refuse("the two top models no longer give one and the same answer")
    values = {(m["value"], m["unit"]) for m in c["measured"]}
    if len(values) != 1:
        refuse("the measured value differs between versions")
    (value, unit), = values
    return {"said": said.pop(), "measured": value, "unit": unit}


def card_facts(data: dict) -> dict:
    """What the card's shortened code and its footer take from the case itself."""
    c = data["control"]
    held = re.search(r"\[(\d+)\]byte", c["snippet"])
    counts = {(m["readings"], m["n"]) for m in c["measured"]}
    if held is None or len(counts) != 1:
        raise ImageError(f"{c['id']}: the snippet or the readings no longer fit the card")
    (readings, n), = counts
    if "hold8k" not in c["snippet"] or f"{n:_}" not in c["snippet"]:
        raise ImageError(f"{c['id']}: the snippet no longer matches the shortened code on the card")
    return {"held_kb": int(held.group(1)) / 1024, "readings": readings, "n": n}


def baseline_label(baseline: dict) -> str:
    return f"author, cold only: {baseline['computed_score']:.2f}"


# ------------------------------------------------------------------ drawing
def px_canvas(width: int, height: int, colors: dict, dpi: int = 100):
    """A figure whose one axes is addressed in pixels, origin top left."""
    fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi, facecolor=colors["surface"])
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")
    return fig, ax


def pt(px: float, dpi: int = 100) -> float:
    return px * 72 / dpi


def text_width(fig, ax, s: str, size_px: float, **kw) -> float:
    t = ax.text(0, 0, s, fontsize=pt(size_px, fig.dpi), **kw)
    width = t.get_window_extent(fig.canvas.get_renderer()).width
    t.remove()
    return width


def fit(fig, ax, s: str, size_px: float, max_width: float, **kw) -> float:
    """The largest font size up to size_px at which s fits into max_width."""
    while size_px > 8 and text_width(fig, ax, s, size_px, **kw) > max_width:
        size_px -= 0.5
    return size_px


def bar_patch(x: float, y: float, length: float, thick: float, rx: float, ry: float, **kw) -> PathPatch:
    """One outline for a horizontal bar: square at the baseline, rounded at the
    data end. rx and ry are the corner radii in the units of each axis."""
    rx, ry = min(rx, length / 2), min(ry, thick / 2)
    y0, y1, x1 = y - thick / 2, y + thick / 2, x + length
    k = 0.5523  # control-point distance of a cubic quarter circle
    verts = [(x, y0), (x1 - rx, y0),
             (x1 - rx + k * rx, y0), (x1, y0 + ry - k * ry), (x1, y0 + ry),
             (x1, y1 - ry),
             (x1, y1 - ry + k * ry), (x1 - rx + k * rx, y1), (x1 - rx, y1),
             (x, y1), (x, y0)]
    codes = [MplPath.MOVETO, MplPath.LINETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4, MplPath.LINETO,
             MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4, MplPath.LINETO, MplPath.CLOSEPOLY]
    return PathPatch(MplPath(verts, codes), linewidth=0, **kw)


def hbar(ax, x: float, y: float, length: float, thick: float, color: str, radius: float = 4) -> None:
    ax.add_patch(bar_patch(x, y, length, thick, radius, radius, facecolor=color))


def save(fig, path: Path, size: tuple[int, int]) -> Path:
    buffer = io.BytesIO()
    try:
        fig.savefig(buffer, format="png", dpi=fig.dpi, facecolor=fig.get_facecolor())
    finally:
        plt.close(fig)
    buffer.seek(0)
    image = Image.open(buffer)
    if image.size != size:
        raise ImageError(f"{path.name} is {image.size}, expected {size}")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path, optimize=True)
    return path


def said_measured_rows(fig, ax, rows: list[dict], x_label: float, x_bar: float, y: float, pitch: float,
                       scale: float, colors: dict, label_px: float, value_px: float, thick: float) -> None:
    room = x_bar - x_label - 16
    for i, row in enumerate(rows):
        cy = y + i * pitch
        look = {"family": row.get("family", SANS), "fontweight": row.get("weight", "normal")}
        if row.get("note"):
            ax.text(x_label, cy - 3, row["label"], fontsize=pt(fit(fig, ax, row["label"], label_px, room, **look)),
                    color=colors["ink"], va="bottom", **look)
            ax.text(x_label, cy + 5, row["note"], color=colors["ink2"], va="top", family=SANS,
                    fontsize=pt(fit(fig, ax, row["note"], label_px * 0.74, room, family=SANS)))
        else:
            ax.text(x_label, cy, row["label"], fontsize=pt(fit(fig, ax, row["label"], label_px, room, **look)),
                    color=colors["ink"], va="center", **look)
        hbar(ax, x_bar, cy, row["value"] * scale, thick, row["color"])
        ax.text(x_bar + row["value"] * scale + 14, cy, row["text"], fontsize=pt(value_px), color=colors["ink"],
                va="center", family=SANS, fontweight="bold")


def draw_cover(data: dict, out: Path = OUT) -> Path:
    width, height, margin, c = 1000, 420, 48, DARK
    pair = control_pair(data)
    fig, ax = px_canvas(width, height, c)
    inner = width - 2 * margin
    size = min(fit(fig, ax, line, 44, inner, family=SANS, fontweight="bold") for line in TITLE)
    for i, line in enumerate(TITLE):
        ax.text(margin, 40 + i * size * 1.28, line, fontsize=pt(size), color=c["ink"], va="top", family=SANS,
                fontweight="bold")
    counts = (f"{data['cases']} memory questions, {data['models']} models, {data['runtime_versions']} runtime versions, "
              "every answer measured")
    y = 40 + 2 * size * 1.28 + 14
    ax.text(margin, y, counts, fontsize=pt(fit(fig, ax, counts, 24, inner, family=SANS)), color=c["ink2"], va="top",
            family=SANS)
    ax.plot([margin, width - margin], [y + 50, y + 50], color=c["grid"], linewidth=1)
    ax.text(margin, y + 72, "Control case: stack of each new idle goroutine", fontsize=pt(18), color=c["ink2"],
            va="top", family=SANS)
    unit = pair["unit"]
    x_bar = margin + 310
    scale = (width - margin - x_bar - 130) / max(pair["said"], pair["measured"])
    said_measured_rows(fig, ax, [
        {"label": "the two top models said", "value": pair["said"], "text": f"{pair['said']:g} {unit}", "color": c["orange"]},
        {"label": "the harness measured", "value": pair["measured"], "text": f"{pair['measured']:.1f} {unit}", "color": c["blue"]},
    ], margin, x_bar, y + 132, 60, scale, c, 23, 31, 34)
    return save(fig, out / "cover.png", (width, height))


def draw_card(data: dict, out: Path = OUT) -> Path:
    width, height, margin, c = 1200, 675, 56, LIGHT
    pair = control_pair(data)
    facts = card_facts(data)
    control = data["control"]
    measured = control["measured"]
    fig, ax = px_canvas(width, height, c)
    ax.text(margin, 44, f"Prediction against measurement, control case {control['id']}", fontsize=pt(18),
            color=c["ink2"], va="top", family=SANS)
    ax.text(margin, 78, "How much stack does a new idle goroutine take?", fontsize=pt(36), color=c["ink"], va="top",
            family=SANS, fontweight="bold")

    code = [
        (f"for i := 0; i < {facts['n']:_}; i++ {{ go hold8k() }}",
         f"// each keeps {facts['held_kb']:g} KB on its stack and stays alive"),
        ("runtime.GC()", ""),
        (f"for i := 0; i < {facts['n']:_}; i++ {{ go idle() }}", "// each blocks on a channel: how much stack?"),
    ]
    top = 150
    ax.add_patch(Rectangle((margin, top), width - 2 * margin, 124, linewidth=0, facecolor=c["panel"]))
    col = margin + 22 + max(text_width(fig, ax, left, 18, family=MONO) for left, _ in code) + 28
    for i, (left, comment) in enumerate(code):
        ax.text(margin + 22, top + 24 + i * 32, left, fontsize=pt(18), color=c["ink"], va="top", family=MONO)
        ax.text(col, top + 24 + i * 32, comment, fontsize=pt(18), color=c["ink2"], va="top", family=MONO)
    ax.text(margin, top + 136, f"The code, shortened. The full case is {control['id']} in cases/cases.json.",
            fontsize=pt(15), color=c["ink2"], va="top", family=SANS)

    unit = pair["unit"]
    x_bar = margin + 400
    scale = (width - margin - x_bar - 110) / max(pair["said"], pair["measured"])
    rows = [{"label": m["model"], "family": MONO, "weight": "bold",
             "note": "said, at confidence " + ", ".join(str(x) for x in m["confidences"]),
             "value": pair["said"], "text": f"{pair['said']:g} {unit}", "color": c["orange"]} for m in control["said"]]
    rows.append({"label": "the harness", "weight": "bold",
                 "note": "measured on " + short_versions([m["version"] for m in measured]),
                 "value": pair["measured"], "text": f"{pair['measured']:.1f} {unit}", "color": c["blue"]})
    said_measured_rows(fig, ax, rows, margin, x_bar, 372, 76, scale, c, 21, 28, 30)
    ax.plot([x_bar, x_bar], [372 - 34, 372 + 2 * 76 + 34], color=c["axis"], linewidth=1)

    foot = [
        f"Confidence as each model stated it in the {', '.join(CONDITIONS[:-1])} and {CONDITIONS[-1]} tasks; "
        "both gave the same answer in all three.",
        f"Measured: {control['metric']} per goroutine over {facts['n']:,} goroutines, the median of "
        f"{facts['readings']} readings on each Go version.",
    ]
    for i, line in enumerate(foot):
        ax.text(margin, 592 + i * 26, line, fontsize=pt(fit(fig, ax, line, 16, width - 2 * margin, family=SANS)),
                color=c["ink2"], va="top", family=SANS)
    return save(fig, out / "card-goroutine.png", (width, height))


def draw_leaderboard(data: dict, out: Path = OUT) -> Path:
    c, dpi, width = LIGHT, 160, 1600
    board = data["board"]
    baseline = data["baseline"]
    slot, head, foot = 78, 250, 120
    height = head + slot * len(board) + foot
    fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi, facecolor=c["surface"])
    probe = fig.add_axes((0, 0, 1, 1))
    probe.axis("off")
    longest = max(text_width(fig, probe, row["model"], 11.5 * dpi / 72, family=SANS) for row in board)
    probe.remove()
    left, right = max(300, round(longest) + 70), 150
    if width - left - right < 600:
        raise ImageError("a model slug is too long for the chart")
    ax = fig.add_axes((left / width, foot / height, (width - left - right) / width, slot * len(board) / height))
    ax.set_facecolor(c["surface"])
    ax.set_xlim(0, 1)
    ax.set_ylim(len(board) - 0.5, -0.5)
    px_x = (width - left - right) / 1.0  # pixels per unit of score
    thick = 44 / slot  # bar thickness in slots
    for i, row in enumerate(board):
        ax.add_patch(bar_patch(0, i, row["mean"], thick, 8 / px_x, 8 / slot, facecolor=c["bar"], zorder=2))
        ax.text(1.03, i, f"{row['mean']:.2f}", fontsize=12, color=c["ink"], va="center", ha="left", family=SANS,
                fontweight="bold", transform=ax.transData, clip_on=False)
    marks = {"cold": ("o", c["blue"], -17), "versioned": ("s", c["orange"], 0), "evidence": ("D", c["aqua"], 17)}
    for cond, (shape, color, offset) in marks.items():
        ax.scatter([row[cond] for row in board], [i + offset / slot for i in range(len(board))], marker=shape,
                   s=62 if shape != "D" else 48, color=color, edgecolors=c["surface"], linewidths=1.8, zorder=4,
                   clip_on=False)
    ax.set_yticks(range(len(board)), [row["model"] for row in board], fontsize=11.5, family=SANS, color=c["ink"])
    ax.set_xticks([0, 0.2, 0.4, 0.6, 0.8, 1.0], ["0", "0.2", "0.4", "0.6", "0.8", "1.0"], fontsize=10.5,
                  color=c["ink2"], family=SANS)
    ax.set_xlabel("task score: answer, estimate and mechanism, one third each", fontsize=10.5, color=c["ink2"],
                  family=SANS, labelpad=8)
    ax.grid(axis="x", color=c["grid"], linewidth=0.6, zorder=0)
    ax.tick_params(axis="both", length=0, pad=8)
    for side in ("top", "right", "bottom"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(c["axis"])
    ax.text(1.03, -0.86, "mean", fontsize=10.5, color=c["ink2"], va="center", ha="left", family=SANS, clip_on=False)

    handles = [Patch(facecolor=c["bar"], label="bar: mean of the three tasks")]
    handles += [Line2D([], [], linestyle="", marker=shape, markersize=8, color=color, markeredgecolor=c["surface"],
                       label=cond) for cond, (shape, color, _) in marks.items()]
    if baseline:
        score = baseline["computed_score"]
        ax.plot([score, score], [-0.62, len(board) - 0.5], color=c["ink"], linewidth=1.2, linestyle=(0, (5, 3)),
                zorder=3, clip_on=False)
        ax.text(score, -0.74, baseline_label(baseline), fontsize=10.5, color=c["ink"], va="bottom",
                ha="center" if score < 0.8 else "right", family=SANS, clip_on=False)
        handles.append(Line2D([], [], color=c["ink"], linewidth=1.2, linestyle=(0, (5, 3)), label="author, cold only"))
    fig.text(40 / width, 1 - 52 / height, "Mean task score per model", fontsize=17, color=c["ink"], family=SANS,
             fontweight="bold", va="top")
    fig.text(40 / width, 1 - 100 / height,
             f"{data['cases']} memory questions asked three ways, scored against measured values. "
             "One complete run per model and task.", fontsize=11, color=c["ink2"], family=SANS, va="top")
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(28 / width, 1 - 132 / height), ncol=len(handles),
               frameon=False, fontsize=10.5, labelcolor=c["ink2"], handletextpad=0.5, columnspacing=1.6,
               prop={"family": SANS, "size": 10.5})
    return save(fig, out / "leaderboard.png", (width, height))


def write_all(out: Path = OUT, runs_root: Path = RUNS, results_dir: Path = RESULTS) -> list[Path]:
    """The chart does not depend on the control case, so it is drawn first.
    The cover and the card are checked together before either is written."""
    with plt.style.context("default"):
        data = collect(runs_root, results_dir)
        board = draw_leaderboard(data, out)
        control_pair(data)
        card_facts(data)
        return [draw_cover(data, out), board, draw_card(data, out)]


def main(argv: list[str]) -> int:
    try:
        paths = write_all()
    except ImageError as exc:
        print(f"images refused: {exc}", file=sys.stderr)
        return 1
    for path in paths:
        with Image.open(path) as im:
            print(f"wrote {path.relative_to(ROOT)} ({im.size[0]} x {im.size[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
