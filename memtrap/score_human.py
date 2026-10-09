"""Score the human baseline against the cold condition.

    uv run python -m memtrap.score_human human/answers.json

answers.json maps a case id to {"answer", "estimate", "mechanism",
"confidence"}. A missing case scores zero, like a parse failure for a model.
"""

from __future__ import annotations

import json
import sys

from memtrap.catalog import LETTERS, active_cases, load_cases, load_truth
from memtrap.score import score_response, summarize


def normalize(entry) -> dict | None:
    if not isinstance(entry, dict):
        return None
    out = {}
    answer = str(entry.get("answer", "")).strip().upper()
    mechanism = str(entry.get("mechanism", "")).strip().upper()
    out["answer"] = answer if answer in LETTERS else None
    out["mechanism"] = mechanism if mechanism in LETTERS else None
    est = entry.get("estimate")
    out["estimate"] = float(est) if isinstance(est, (int, float)) and not isinstance(est, bool) else None
    conf = entry.get("confidence")
    out["confidence"] = int(conf) if isinstance(conf, (int, float)) and 0 <= conf <= 100 else None
    return out


def score_answers(answers: dict, cases: list[dict], truth: dict) -> tuple[list[dict], dict]:
    rows = []
    known = {c["id"] for c in active_cases(cases)}
    for cid in sorted(set(answers) - known):
        print(f"warning: answers.json has an unknown case id {cid!r}; it is ignored", file=sys.stderr)
    for case in active_cases(cases):
        if case["id"] not in truth:
            raise SystemExit(f"no measured truth for {case['id']} yet; score after Phase 2")
        rows.append(score_response(case, truth[case["id"]], "cold", None, normalize(answers.get(case["id"]))))
    return rows, summarize(rows)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip())
        return 2
    with open(argv[1]) as fh:
        answers = json.load(fh)
    rows, summary = score_answers(answers, load_cases(), load_truth())
    for r in rows:
        print(f"{r['case']}: answer {r['answer'] or '-'} (measured {r['expected_answer']}) "
              f"mechanism {r['mechanism'] or '-'} (correct {r['correct_mechanism']}) "
              f"estimate {'ok' if r['estimate_ok'] else 'off'}  score {r['case_score']:.2f}")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
