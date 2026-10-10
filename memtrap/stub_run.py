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
import threading
from collections import Counter
from pathlib import Path

os.environ.setdefault("BENCHMARK_CONSOLE_QUIET", "True")

import kaggle_benchmarks as kbench  # noqa: E402
from kaggle_benchmarks.actors.llms import LLMChat, LLMResponse  # noqa: E402

from memtrap.build_tasks import OUT_DIR, TASKS  # noqa: E402

STUBS = ("correct", "folklore", "garbage")
REPAIR_PREFIX = "Your reply could not be read."
# The text of the model proxy's refusal when a call's reservation does not fit the quota.
QUOTA_MESSAGE = (
    "Error code: 403 - {'message': 'The max estimated cost of operation ($1.28) exceeds your available "
    "quota (based on max_output_tokens). Set max_output_tokens in your request to lower the reservation, "
    "or try again later.'}"
)


class StubLLM(LLMChat):
    """Answers each prompt from the item it belongs to."""

    def __init__(self, mode: str, items: dict[str, dict]):
        super().__init__(name=f"stub-{mode}")
        self.model = f"stub-{mode}"
        self.mode = mode
        self.by_prompt = {item["prompt"]: item for item in items.values()}
        self.lock = threading.Lock()
        self.calls: Counter = Counter()  # calls per item prompt
        self.caps: list = []  # the max_tokens of every call
        self.last_messages: list = []  # the last message of every call

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
        texts = [str(m.content) for m in messages]
        last = texts[-1] if texts else ""
        if self.mode in ("quota", "badcap", "down"):
            # Failing calls: a quota refusal twice per item, a provider that
            # rejects the output cap, a proxy that never answers.
            item = next((self.by_prompt[t] for t in reversed(texts) if t in self.by_prompt), None)
            with self.lock:
                self.calls[item["prompt"] if item else last] += 1
                n = self.calls[item["prompt"] if item else last]
                self.caps.append(kwargs.get("max_tokens"))
                self.last_messages.append(last)
            if self.mode == "down":
                raise RuntimeError("Connection error.")
            if self.mode == "quota" and n <= 2:
                raise RuntimeError(QUOTA_MESSAGE)
            if self.mode == "badcap" and "max_tokens" in kwargs:
                raise RuntimeError("Error code: 400 - {'message': 'max_tokens is too large for this model'}")
            return LLMResponse(content=self.correct_reply(item), meta={"input_tokens": 0})
        if self.mode == "repair":
            # Garbage first, a correct JSON reply after the repair message.
            if last.startswith(REPAIR_PREFIX):
                item = next((self.by_prompt[t] for t in reversed(texts) if t in self.by_prompt), None)
                return LLMResponse(content=self.correct_reply(item), meta={"input_tokens": 0})
            return LLMResponse(content="Let me think about the allocator first.", meta={"input_tokens": 0})
        if self.mode == "draft":
            # A draft object, then the final one in the same reply.
            item = self.by_prompt.get(last)
            draft = json.dumps({"answer": "A", "estimate": 1, "mechanism": "A", "confidence": 1})
            return LLMResponse(content=f"Draft: {draft}\nFinal:\n{self.correct_reply(item)}", meta={"input_tokens": 0})
        return LLMResponse(content=self.reply_for(self.by_prompt.get(last)), meta={"input_tokens": 0})

    def correct_reply(self, item: dict | None) -> str:
        mode, self.mode = self.mode, "correct"
        try:
            return self.reply_for(item)
        finally:
            self.mode = mode


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
