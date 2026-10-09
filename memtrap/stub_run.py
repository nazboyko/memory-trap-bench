"""Run the generated task files against deterministic stub models, offline.

    uv run python -m memtrap.stub_run

Three stubs: "correct" answers with the measured truth, "folklore" answers
with the rule of thumb, "garbage" never returns JSON. Each task file is
executed up to its final .run() cell, then its task is run once per stub in
a temporary directory, so no run files land in the repository.

Expected: correct scores 1.0; folklore scores under 0.4 and picks the
folklore mechanism on every non-control item; garbage scores 0 with a parse
failure on every item. Exit code 1 if any expectation fails.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

import kaggle_benchmarks as kbench
from kaggle_benchmarks.actors.llms import LLMChat, LLMResponse

from memtrap.build_tasks import OUT_DIR, TASKS

STUBS = ("correct", "folklore", "garbage")


class StubLLM(LLMChat):
    """Answers each prompt from the item it belongs to."""

    def __init__(self, mode: str, items: dict[str, dict]):
        super().__init__(name=f"stub-{mode}")
        self.model = f"stub-{mode}"
        self.mode = mode
        self.by_prompt = {item["prompt"]: item for item in items.values()}

    def reply_for(self, item: dict | None) -> str:
        if self.mode == "garbage" or item is None:
            return "I would need to run the code to know."
        truth = item["truth"]
        if self.mode == "correct":
            answer = truth["cold_answer"] if item["version"] is None else truth["versions"][item["version"]]["answer"]
            version = item["version"] or item["case"]["versions"][-1]
            reply = {
                "answer": answer,
                "estimate": truth["versions"][version]["estimate"],
                "mechanism": item["case"]["correct_mechanism"],
                "confidence": 90,
                "reasoning": "stub",
            }
        else:
            reply = {
                "answer": item["case"]["folklore_answer"],
                "estimate": 0.001,
                "mechanism": item["case"]["folklore_mechanism"],
                "confidence": 90,
                "reasoning": "stub",
            }
        return "```json\n" + json.dumps(reply) + "\n```"

    def invoke(self, messages, system=None, reasoning=None, tools=None, **kwargs):
        last = str(messages[-1].content) if messages else ""
        return LLMResponse(content=self.reply_for(self.by_prompt.get(last)), meta={"input_tokens": 0})


def load_task(path: Path, function: str) -> dict:
    """Execute the task file up to (not including) its final .run() cell."""
    source = path.read_text()
    cut = source.index(f"{function}.run(kbench.llm)")
    namespace: dict = {"__name__": "memtrap_task"}
    exec(compile(source[:cut], str(path), "exec", dont_inherit=True), namespace)
    return namespace


@contextlib.contextmanager
def in_directory(path: str):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def run_stub(namespace: dict, function: str, mode: str) -> dict:
    items = namespace["ITEMS"]
    task = namespace[function]
    run = task.run(StubLLM(mode, items))
    rows = [sub.result for sub in run.subruns if isinstance(sub.result, dict)]
    summary = namespace["summarize"](rows)
    summary["task_score"] = run.result
    summary["items"] = len(items)
    summary["non_control_items"] = sum(not item["case"]["control"] for item in items.values())
    return summary


def check(mode: str, s: dict) -> list[str]:
    problems = []
    if mode == "correct" and abs(s["task_score"] - 1.0) > 1e-9:
        problems.append(f"correct stub scored {s['task_score']}")
    if mode == "folklore":
        if not s["task_score"] < 0.4:
            problems.append(f"folklore stub scored {s['task_score']}")
        if s["folklore_mechanism"] != s["non_control_items"]:
            problems.append(f"folklore stub: folklore_mechanism {s['folklore_mechanism']} != {s['non_control_items']}")
    if mode == "garbage":
        if s["task_score"] != 0 or s["parse_failures"] != s["items"]:
            problems.append(f"garbage stub: score {s['task_score']}, parse failures {s['parse_failures']}/{s['items']}")
    if s["cases"] != s["items"]:
        problems.append(f"{mode} stub: {s['items'] - s['cases']} items errored")
    return problems


def main(argv: list[str]) -> int:
    problems: list[str] = []
    os.environ.setdefault("BENCHMARK_CONSOLE_QUIET", "1")
    with tempfile.TemporaryDirectory() as tmp, in_directory(tmp):
        for condition, spec in TASKS.items():
            path = OUT_DIR / spec["file"]
            namespace = load_task(path, spec["function"])
            for mode in STUBS:
                s = run_stub(namespace, spec["function"], mode)
                print(
                    f"{condition:9} {mode:8} score={s['task_score']:.3f} items={s['items']} "
                    f"folklore_mech={s['folklore_mechanism']}/{s['non_control_items']} "
                    f"parse_failures={s['parse_failures']} confidently_wrong={s['confidently_wrong']}"
                )
                problems += [f"{condition}: {p}" for p in check(mode, s)]
    for p in problems:
        print(f"FAIL {p}")
    print("stub run ok" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
