"""Prompt text for one case under one condition.

Conditions:
  cold       code and question only
  versioned  code, question and the runtime version
  evidence   versioned prompt plus one measured fact from another case
"""

from __future__ import annotations

from memtrap.catalog import version_label

CONDITIONS = ("cold", "versioned", "evidence")

RUNTIME_LINES = {
    "php": "Runtime: {label}.x, CLI, NTS, opcache disabled, memory measured with memory_get_usage() (1 MB = 1,048,576 bytes).",
    "go": "Runtime: {label}.x, linux/amd64, memory read from runtime.MemStats (1 MB = 1,048,576 bytes, 1 KB = 1,024 bytes).",
}

LANGUAGE = {"php": "php", "go": "go"}

JSON_INSTRUCTION = (
    "Reply with one JSON object and nothing else, in this shape:\n"
    '{{"answer": "A", "estimate": 0.0, "mechanism": "A", "confidence": 50, "reasoning": "..."}}\n'
    "answer: the letter of the answer option. estimate: a number in {unit}. "
    "mechanism: the letter of the explanation. confidence: an integer from 0 to 100 "
    "for how sure you are about the answer. reasoning: at most 60 words."
)


def code_block(case: dict) -> str:
    lang = LANGUAGE[case["runtime"]]
    blocks = [f"```{lang}\n{case['snippet_a']}\n```"]
    if case["snippet_b"].strip():
        blocks.append(f"```{lang}\n{case['snippet_b']}\n```")
    return "\n\n".join(blocks)


def options_block(options: dict[str, str]) -> str:
    return "\n".join(f"{k}. {v}" for k, v in sorted(options.items()))


def case_body(case: dict, version: str | None = None, evidence: str | None = None) -> str:
    """Everything a reader sees for one case, without the reply instruction."""
    parts = [code_block(case)]
    if version is not None:
        parts.append(RUNTIME_LINES[case["runtime"]].format(label=version_label(version)))
    if evidence is not None:
        parts.append(f"Measured fact: {evidence}")
    parts.append(f"Question: {case['question']}")
    parts.append("Answer options:\n" + options_block(case["options"]))
    parts.append("Which explanation best describes the cause?\n" + options_block(case["mechanisms"]))
    parts.append(f"Estimate: {case['estimate']['what']}.")
    return "\n\n".join(parts)


def render_prompt(case: dict, condition: str, version: str | None = None, evidence: str | None = None) -> str:
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition {condition!r}")
    if condition == "cold":
        version, evidence = None, None
    elif version is None:
        raise ValueError(f"{condition} prompt for {case['id']} needs a version")
    if condition == "versioned":
        evidence = None
    elif condition == "evidence" and not evidence:
        raise ValueError(f"evidence prompt for {case['id']} needs a measured fact")
    lang = "PHP" if case["runtime"] == "php" else "Go"
    head = f"A question about memory use in {lang}. Read the code, then answer."
    tail = JSON_INSTRUCTION.format(unit=case["estimate"]["unit"])
    return "\n\n".join([head, case_body(case, version, evidence), tail])
