"""Scoring of one parsed response against the measured truth. Pure functions,
no LLM judge.

A response is a dict with answer, estimate, mechanism, confidence (any of
them may be missing or invalid), or None when nothing could be parsed.
"""

from __future__ import annotations

CONFIDENTLY_WRONG = 80


def expected_answer(case: dict, truth_case: dict, condition: str, version: str | None) -> str:
    if condition == "cold":
        return truth_case["cold_answer"]
    return truth_case["versions"][version]["answer"]


def expected_estimates(case: dict, truth_case: dict, condition: str, version: str | None) -> list[float]:
    """Cold accepts the measured value of any version the case names."""
    if condition == "cold":
        return [truth_case["versions"][v]["estimate"] for v in case["versions"]]
    return [truth_case["versions"][version]["estimate"]]


def estimate_ok(estimate, targets: list[float], tolerance: list[float]) -> bool:
    if not isinstance(estimate, (int, float)) or isinstance(estimate, bool):
        return False
    lo, hi = tolerance
    for target in targets:
        if target == 0:
            if abs(estimate) < 1e-9:
                return True
            continue
        if target > 0 and lo * target <= estimate <= hi * target:
            return True
    return False


def score_response(case: dict, truth_case: dict, condition: str, version: str | None, response: dict | None) -> dict:
    answer = expected_answer(case, truth_case, condition, version)
    targets = expected_estimates(case, truth_case, condition, version)
    out = {
        "case": case["id"],
        "condition": condition,
        "version": version,
        "control": case["control"],
        "expected_answer": answer,
        "expected_estimate": targets,
        "correct_mechanism": case["correct_mechanism"],
        "parse_failure": response is None,
    }
    r = response or {}
    out["answer"] = r.get("answer")
    out["estimate"] = r.get("estimate")
    out["mechanism"] = r.get("mechanism")
    out["confidence"] = r.get("confidence")
    out["answer_ok"] = int(r.get("answer") == answer)
    out["estimate_ok"] = int(estimate_ok(r.get("estimate"), targets, case["estimate"]["tolerance"]))
    out["mechanism_ok"] = int(r.get("mechanism") == case["correct_mechanism"])
    out["case_score"] = (out["answer_ok"] + out["estimate_ok"] + out["mechanism_ok"]) / 3
    # A control's folklore is the correct mechanism, so it is never counted as folklore.
    out["folklore_mechanism"] = int(not case["control"] and r.get("mechanism") == case["folklore_mechanism"])
    out["folklore_answer"] = int(not case["control"] and r.get("answer") == case["folklore_answer"])
    out["right_answer_wrong_mechanism"] = int(out["answer_ok"] == 1 and out["mechanism_ok"] == 0 and response is not None)
    conf = r.get("confidence")
    out["confidently_wrong"] = int(
        response is not None and out["answer_ok"] == 0 and isinstance(conf, (int, float)) and conf >= CONFIDENTLY_WRONG
    )
    return out


def summarize(rows: list[dict]) -> dict:
    """Task score and derived counts over scored rows."""
    if not rows:
        return {"score": 0.0, "cases": 0}
    right = [r["confidence"] for r in rows if r["answer_ok"] and isinstance(r["confidence"], (int, float))]
    wrong = [r["confidence"] for r in rows if not r["answer_ok"] and isinstance(r["confidence"], (int, float))]
    return {
        "score": sum(r["case_score"] for r in rows) / len(rows),
        "cases": len(rows),
        "answer_ok": sum(r["answer_ok"] for r in rows),
        "estimate_ok": sum(r["estimate_ok"] for r in rows),
        "mechanism_ok": sum(r["mechanism_ok"] for r in rows),
        "right_answer_wrong_mechanism": sum(r["right_answer_wrong_mechanism"] for r in rows),
        "folklore_mechanism": sum(r["folklore_mechanism"] for r in rows),
        "folklore_answer": sum(r["folklore_answer"] for r in rows),
        "confidently_wrong": sum(r["confidently_wrong"] for r in rows),
        "parse_failures": sum(r["parse_failure"] for r in rows),
        "mean_confidence_right": sum(right) / len(right) if right else None,
        "mean_confidence_wrong": sum(wrong) / len(wrong) if wrong else None,
    }
