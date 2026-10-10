"""Write docs/index.html: a play-along page, one card per case, guess first,
then the measured numbers. Static, no build step, no external requests.

    uv run python -m memtrap.site

Every number on the page comes from cases/truth.json; model picks come from
the run files under results/kaggle/ when they exist.
"""

from __future__ import annotations

import html
import json
import sys
from collections import Counter

from memtrap.analyze import choose_runs, load_runs
from memtrap.catalog import ROOT, active_cases, load_cases, load_truth, version_label

OUT = ROOT / "docs" / "index.html"
REPO = "https://github.com/nazboyko/memory-trap-bench"


def model_picks(cases: list[dict], truth: dict) -> dict:
    """Cold-condition answer letters per case across models, if runs exist."""
    runs_root = ROOT / "results" / "kaggle"
    if not runs_root.exists():
        return {}
    chosen = choose_runs(load_runs(runs_root), cases, truth)
    picks: dict = {}
    for (cond, model), entry in sorted(chosen.items()):
        if cond != "cold" or not entry["complete"]:
            continue
        for r in entry["rows"]:
            picks.setdefault(r["case"], []).append({"model": model, "answer": r["answer"], "ok": r["answer_ok"]})
    return picks


def case_data(cases: list[dict], truth: dict) -> list[dict]:
    picks = model_picks(cases, truth)
    out = []
    for c in active_cases(cases):
        t = truth[c["id"]]
        measured = []
        for v in c["versions"]:
            e = t["versions"][v]
            measured.append({
                "version": version_label(v),
                "answer": e["answer"],
                "text": c["options"][e["answer"]],
                "quantities": {k: round(x, 2) for k, x in e["quantities"].items()},
                "estimate": e["estimate"],
                "unit": e["estimate_unit"],
                "runtime_version": e["runtime_versions"][0],
            })
        model = picks.get(c["id"], [])
        out.append({
            "id": c["id"],
            "runtime": c["runtime"],
            "family": c["family"],
            "control": c["control"],
            "snippets": [s for s in (c["snippet_a"], c["snippet_b"]) if s.strip()],
            "question": c["question"],
            "options": c["options"],
            "cold": t["cold_answer"],
            "split": t["version_split"],
            "folklore": c["folklore_answer"],
            "mechanism": c["mechanisms"][c["correct_mechanism"]],
            "why": c["why_it_matters"],
            "metric": t["metric"],
            "estimate_what": c["estimate"]["what"].rsplit(", in ", 1)[0],
            "measured": measured,
            "models": {"n": len(model), "counts": dict(Counter(p["answer"] or "-" for p in model)),
                       "right": sum(p["ok"] for p in model)},
        })
    return out


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MemoryTrap Bench</title>
<meta name="description" content="Seventeen memory questions about PHP arrays, Go maps and goroutine stacks. Guess, then see what the runtime measured.">
<style>
:root {
  color-scheme: light;
  --paper: #f7f8f5;
  --grid: #e4e9ea;
  --ink: #1d2a3a;
  --muted: #5b6874;
  --rule: #c8372d;
  --mark: #ffe45c;
  --mark-ink: #1d2a3a;
  --code-bg: #eef1f0;
  --line: #cfd6d8;
  --php: #6f73ad;
  --go: #007d9c;
  --ok: #1f7a4d;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --paper: #121a24;
    --grid: #1b2633;
    --ink: #e4ebf2;
    --muted: #9aa8b5;
    --rule: #ff7a6e;
    --mark: #d9b92e;
    --mark-ink: #121a24;
    --code-bg: #182230;
    --line: #2a3747;
    --php: #a3a7e0;
    --go: #4cc7e6;
    --ok: #5fd39a;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --paper: #121a24; --grid: #1b2633; --ink: #e4ebf2; --muted: #9aa8b5; --rule: #ff7a6e;
  --mark: #d9b92e; --mark-ink: #121a24; --code-bg: #182230; --line: #2a3747;
  --php: #a3a7e0; --go: #4cc7e6; --ok: #5fd39a;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0;
  background-color: var(--paper);
  background-image:
    linear-gradient(var(--grid) 1px, transparent 1px),
    linear-gradient(90deg, var(--grid) 1px, transparent 1px);
  background-size: 24px 24px;
  color: var(--ink);
  font-family: Charter, "Iowan Old Style", "Palatino Linotype", Georgia, serif;
  font-size: 18px;
  line-height: 1.6;
}
main { max-width: 46rem; margin: 0 auto; padding: 3rem 1rem 5rem; }
header h1 { font-size: 2.6rem; line-height: 1.1; margin: 0 0 1rem; letter-spacing: -0.01em; }
header p { margin: 0 0 1rem; max-width: 38rem; }
.score { position: sticky; top: 0; z-index: 2; margin: 1.5rem -1rem 2rem; padding: 0.6rem 1rem;
  background: var(--paper); border-bottom: 1px solid var(--line); font-size: 1rem; color: var(--muted); }
.score strong { color: var(--ink); }
article { margin: 0 0 3.5rem; }
.head { display: flex; align-items: baseline; gap: 0.75rem; flex-wrap: wrap; margin-bottom: 0.5rem; }
.id { font-weight: 700; font-size: 1.35rem; }
.tag { font-size: 0.85rem; padding: 0 0.5rem; border-radius: 3px; color: var(--paper); }
.tag.php { background: var(--php); }
.tag.go { background: var(--go); }
.control { font-size: 0.9rem; color: var(--muted); font-style: italic; }
pre { margin: 0.75rem 0; padding: 0.9rem 1rem; background: var(--code-bg); border-left: 3px solid var(--line);
  overflow-x: auto; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.82rem; line-height: 1.5; tab-size: 4; }
.q { margin: 1rem 0 0.75rem; }
.opts { display: grid; gap: 0.5rem; }
.opts button {
  font: inherit; font-size: 1rem; text-align: left; color: var(--ink); background: var(--paper);
  border: 1px solid var(--line); border-radius: 4px; padding: 0.55rem 0.8rem; cursor: pointer;
  display: flex; gap: 0.75rem; align-items: baseline;
}
.opts button .k { font-weight: 700; min-width: 1.1rem; }
.opts button:hover:not(:disabled) { border-color: var(--ink); }
.opts button:focus-visible { outline: 3px solid var(--go); outline-offset: 2px; }
.opts button:disabled { cursor: default; }
.opts button.picked { border-color: var(--ink); border-width: 2px; }
.opts button.right .k::after { content: " \\2713"; color: var(--ok); }
.opts button.folk:not(.right) .t { text-decoration: line-through; text-decoration-color: var(--rule); text-decoration-thickness: 2px; }
.reveal { margin-top: 1rem; padding-top: 0.25rem; }
.reveal[hidden] { display: none; }
.verdict { font-weight: 700; margin: 0.5rem 0; }
.measure { margin: 0.5rem 0; padding: 0; list-style: none; }
.measure li { margin: 0.35rem 0; }
.mark { background: linear-gradient(transparent 12%, var(--mark) 12%, var(--mark) 88%, transparent 88%);
  color: var(--mark-ink); padding: 0 0.25rem; font-weight: 700; }
.small { font-size: 0.95rem; color: var(--muted); }
.rule { color: var(--rule); }
footer { border-top: 1px solid var(--line); padding-top: 1rem; font-size: 0.95rem; color: var(--muted); }
a { color: inherit; }
@media (max-width: 560px) {
  body { font-size: 17px; }
  header h1 { font-size: 2rem; }
}
@media (prefers-reduced-motion: no-preference) {
  .reveal:not([hidden]) { animation: show 0.25s ease-out; }
  @keyframes show { from { opacity: 0; } to { opacity: 1; } }
}
</style>
</head>
<body>
<main>
<header>
<h1>MemoryTrap Bench</h1>
<p>Seventeen questions about memory in PHP arrays, Go maps and goroutine stacks. Pick an answer first. Then the page shows what a harness measured on real runtime versions, and which rule of thumb it broke.</p>
<p class="small">No runtime version is given in the question, the same way the models saw it in the cold condition. When the measured answer changes between versions, the right answer is the version option. Sizes are binary: 1 MB = 1,048,576 bytes.</p>
</header>
<div class="score" aria-live="polite">Your guesses: <strong id="right">0</strong> right of <strong id="done">0</strong> answered, <span id="left"></span> to go. <button id="reset" type="button" class="small" style="font:inherit;background:none;border:0;text-decoration:underline;cursor:pointer;color:inherit">Start over</button></div>
<div id="cases"></div>
<footer>
<p>Measured on GitHub Actions (PHP 8.1 to 8.4 CLI with opcache off, Go 1.23 to 1.26 on linux/amd64), three readings each, median. Code, raw readings and model replies: <a href="__REPO__">__REPO__</a>.</p>
</footer>
</main>
<script>
const CASES = __DATA__;
const KEY = "memtrap-guesses-v1";
let guesses = {};
try { guesses = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (e) { guesses = {}; }
function save() { try { localStorage.setItem(KEY, JSON.stringify(guesses)); } catch (e) {} }
function el(tag, attrs, text) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
  if (text !== undefined) n.textContent = text;
  return n;
}
function fmtQ(q) { return "r" in q ? "ratio " + q.r + "; " : ""; }
function tally() {
  const ids = Object.keys(guesses).filter(id => CASES.some(c => c.id === id));
  const right = ids.filter(id => guesses[id] === CASES.find(c => c.id === id).cold).length;
  document.getElementById("right").textContent = right;
  document.getElementById("done").textContent = ids.length;
  document.getElementById("left").textContent = (CASES.length - ids.length) + " of " + CASES.length;
}
function reveal(card, c, pick) {
  card.querySelectorAll(".opts button").forEach(b => {
    b.disabled = true;
    const k = b.dataset.k;
    if (k === c.cold) b.classList.add("right");
    if (k === c.folklore) b.classList.add("folk");
    if (k === pick) b.classList.add("picked");
  });
  const r = card.querySelector(".reveal");
  r.hidden = false;
  r.querySelector(".verdict").textContent = pick === c.cold
    ? "Right. The answer is " + c.cold + "."
    : "The answer is " + c.cold + ". You picked " + pick + ".";
}
function render() {
  const root = document.getElementById("cases");
  root.textContent = "";
  for (const c of CASES) {
    const card = el("article", {id: c.id});
    const head = el("div", {class: "head"});
    head.append(el("span", {class: "id"}, c.id), el("span", {class: "tag " + c.runtime}, c.runtime === "php" ? "PHP " + c.family : "Go " + c.family));
    if (c.control) head.append(el("span", {class: "control"}, "control case: the rule of thumb holds here"));
    card.append(head);
    for (const s of c.snippets) { const pre = el("pre"); pre.append(el("code", {}, s)); card.append(pre); }
    card.append(el("p", {class: "q"}, c.question));
    const opts = el("div", {class: "opts", role: "group", "aria-label": "Answer options for " + c.id});
    for (const [k, t] of Object.entries(c.options)) {
      const b = el("button", {type: "button", "data-k": k});
      b.append(el("span", {class: "k"}, k), el("span", {class: "t"}, t));
      b.addEventListener("click", () => { guesses[c.id] = k; save(); reveal(card, c, k); tally(); });
      opts.append(b);
    }
    card.append(opts);
    const r = el("div", {class: "reveal", hidden: ""});
    r.append(el("p", {class: "verdict"}));
    const ul = el("ul", {class: "measure"});
    for (const m of c.measured) {
      const li = el("li");
      li.append(el("span", {}, m.version + " (" + m.runtime_version + "): "), el("span", {class: "mark"}, m.answer + ". " + m.text));
      li.append(el("span", {class: "small"}, " " + fmtQ(m.quantities) + c.estimate_what + ": " + m.estimate + " " + m.unit));
      ul.append(li);
    }
    r.append(ul);
    if (c.split) r.append(el("p", {}, "The versions disagree, so without a version the right answer is " + c.cold + "."));
    const folk = el("p");
    if (c.control) folk.append(el("span", {}, "Rule of thumb: " + c.folklore + ". " + c.options[c.folklore] + ". It holds."));
    else { folk.append(el("span", {}, "Rule of thumb: "), el("span", {class: "rule"}, c.folklore + ". " + c.options[c.folklore])); }
    r.append(folk);
    r.append(el("p", {}, "Why: " + c.mechanism + "."));
    r.append(el("p", {class: "small"}, c.why));
    const mp = el("p", {class: "small"});
    if (c.models.n) {
      const parts = Object.entries(c.models.counts).sort().map(([k, n]) => k + ": " + n);
      mp.textContent = "Models, cold condition: " + c.models.right + " of " + c.models.n + " picked " + c.cold + " (" + parts.join(", ") + ").";
    } else {
      mp.textContent = "Model replies for this case are not in the repository yet.";
    }
    r.append(mp);
    card.append(r);
    root.append(card);
    if (guesses[c.id]) reveal(card, c, guesses[c.id]);
  }
  tally();
}
document.getElementById("reset").addEventListener("click", () => { guesses = {}; save(); render(); });
render();
</script>
</body>
</html>
"""


def render_page(data: list[dict]) -> str:
    payload = json.dumps(data, ensure_ascii=True).replace("</", "<\\/")
    return PAGE.replace("__DATA__", payload).replace("__REPO__", html.escape(REPO))


def main(argv: list[str]) -> int:
    cases, truth = load_cases(), load_truth()
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(render_page(case_data(cases, truth)))
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
