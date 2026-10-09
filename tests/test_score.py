from memtrap.catalog import by_id
from memtrap.score import estimate_ok, score_response, summarize
from memtrap.score_human import normalize, score_answers


def truth_p1():
    return {
        "cold_answer": "D",
        "versions": {
            "php-8.1": {"answer": "B", "estimate": 40.0},
            "php-8.3": {"answer": "C", "estimate": 40.0},
        },
    }


def test_estimate_tolerance():
    assert estimate_ok(40, [40.0], [0.67, 1.5])
    assert estimate_ok(27, [40.0], [0.67, 1.5])
    assert not estimate_ok(26, [40.0], [0.67, 1.5])
    assert not estimate_ok(61, [40.0], [0.67, 1.5])
    assert not estimate_ok("40", [40.0], [0.67, 1.5])
    assert not estimate_ok(True, [1.0], [0.67, 1.5])
    assert estimate_ok(30, [16.0, 32.0], [0.67, 1.5])


def test_cold_needs_the_version_option(cases):
    p1 = by_id(cases)["P1"]
    good = {"answer": "D", "estimate": 40, "mechanism": "C", "confidence": 60}
    row = score_response(p1, truth_p1(), "cold", None, good)
    assert row["case_score"] == 1.0
    row = score_response(p1, truth_p1(), "cold", None, {**good, "answer": "C"})
    assert row["answer_ok"] == 0


def test_versioned_scores_against_its_version(cases):
    p1 = by_id(cases)["P1"]
    r = {"answer": "B", "estimate": 40, "mechanism": "C", "confidence": 90}
    assert score_response(p1, truth_p1(), "versioned", "php-8.1", r)["answer_ok"] == 1
    row = score_response(p1, truth_p1(), "versioned", "php-8.3", r)
    assert row["answer_ok"] == 0 and row["confidently_wrong"] == 1


def test_folklore_and_right_answer_wrong_mechanism(cases):
    p1 = by_id(cases)["P1"]
    r = {"answer": "C", "estimate": 10, "mechanism": "B", "confidence": 50}
    row = score_response(p1, truth_p1(), "versioned", "php-8.3", r)
    assert row["folklore_mechanism"] == 1
    assert row["right_answer_wrong_mechanism"] == 1
    assert row["estimate_ok"] == 0


def test_control_is_never_folklore(cases):
    r1 = by_id(cases)["R1"]
    truth = {"cold_answer": "A", "versions": {"go-1.24": {"answer": "A", "estimate": 2.0}}}
    r = {"answer": "A", "estimate": 2, "mechanism": r1["folklore_mechanism"], "confidence": 95}
    row = score_response(r1, truth, "cold", None, r)
    assert row["folklore_mechanism"] == 0 and row["folklore_answer"] == 0
    assert row["case_score"] == 1.0


def test_parse_failure_scores_zero(cases):
    p1 = by_id(cases)["P1"]
    row = score_response(p1, truth_p1(), "cold", None, None)
    assert row["parse_failure"] and row["case_score"] == 0
    assert row["right_answer_wrong_mechanism"] == 0 and row["confidently_wrong"] == 0


def test_summarize_counts(cases):
    p1 = by_id(cases)["P1"]
    rows = [
        score_response(p1, truth_p1(), "cold", None, {"answer": "D", "estimate": 40, "mechanism": "C", "confidence": 70}),
        score_response(p1, truth_p1(), "cold", None, None),
    ]
    s = summarize(rows)
    assert s["score"] == 0.5 and s["parse_failures"] == 1 and s["mean_confidence_right"] == 70


def test_normalize_human_entry():
    assert normalize({"answer": "b", "estimate": 3, "mechanism": " c ", "confidence": 101}) == {
        "answer": "B", "mechanism": "C", "estimate": 3.0, "confidence": None,
    }
    assert normalize("A") is None


def test_score_answers_missing_case_scores_zero(cases):
    truth = {}
    for c in cases:
        truth[c["id"]] = {
            "cold_answer": "A",
            "versions": {v: {"answer": "A", "estimate": 1.0} for v in c["versions"]},
        }
    rows, summary = score_answers({}, cases, truth)
    assert summary["cases"] == 15 and summary["score"] == 0
