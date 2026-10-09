"""Write the human quiz: the cold condition of every benchmark case, with no
measured values and no answers.

    uv run python -m memtrap.quiz [output path]

The default output is human/quiz.md, which stays out of the repository.
"""

from __future__ import annotations

import sys
from pathlib import Path

from memtrap.catalog import ROOT, active_cases, load_cases
from memtrap.prompts import case_body

DEFAULT_OUT = ROOT / "human" / "quiz.md"

INTRO = """# MemoryTrap Bench: the human quiz

Answer every case before you look at the measurements, the notes or the model
results. Same rules as the models in the cold condition: no runtime version is
given, no tools, no running the code.

For each case replace the question marks with an answer letter, an estimate in the unit the case names,
a mechanism letter and a confidence from 0 to 100. Put them in
`human/answers.json` next to this file, in this shape:

```json
{
  "P1": {"answer": "?", "estimate": 0.0, "mechanism": "?", "confidence": 0},
  "P2": {"answer": "?", "estimate": 0.0, "mechanism": "?", "confidence": 0}
}
```

Then score yourself with:

```
uv run python -m memtrap.score_human human/answers.json
```

Sizes are binary: 1 MB = 1,048,576 bytes, 1 KB = 1,024 bytes.
"""


def render_quiz(cases: list[dict]) -> str:
    parts = [INTRO]
    for case in active_cases(cases):
        lang = "PHP" if case["runtime"] == "php" else "Go"
        parts.append(f"## Case {case['id']} ({lang})\n\n{case_body(case)}\n\n"
                     f"Your answer: ___  Estimate ({case['estimate']['unit']}): ___  "
                     f"Mechanism: ___  Confidence (0-100): ___\n")
    return "\n".join(parts)


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_quiz(load_cases()))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
