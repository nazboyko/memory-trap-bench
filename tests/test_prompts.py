import pytest

from memtrap.catalog import by_id
from memtrap.prompts import render_prompt


def test_cold_prompt_has_no_version(cases):
    p1 = by_id(cases)["P1"]
    text = render_prompt(p1, "cold", version="php-8.3", evidence="ignored")
    assert "Runtime:" not in text and "Measured fact" not in text
    assert p1["snippet_b"] in text and "D. It depends on the PHP version" in text
    assert '"estimate"' in text and "a number in MB" in text


def test_versioned_prompt_names_the_version(cases):
    g1 = by_id(cases)["G1"]
    text = render_prompt(g1, "versioned", version="go-1.24", evidence="ignored")
    assert "Runtime: Go 1.24.x, linux/amd64" in text
    assert "Measured fact" not in text


def test_evidence_prompt_adds_the_fact(cases):
    r2 = by_id(cases)["R2"]
    text = render_prompt(r2, "evidence", version="go-1.24", evidence="100,000 goroutines took 195.7 MB.")
    assert "Measured fact: 100,000 goroutines took 195.7 MB." in text
    assert "a number in KB" in text


@pytest.mark.parametrize("condition", ["versioned", "evidence"])
def test_versioned_conditions_need_a_version(cases, condition):
    with pytest.raises(ValueError):
        render_prompt(by_id(cases)["P1"], condition, evidence="x")


def test_unknown_condition(cases):
    with pytest.raises(ValueError):
        render_prompt(by_id(cases)["P1"], "warm")


def test_prompts_never_contain_measured_values(cases):
    for case in cases:
        text = render_prompt(case, "cold")
        assert "truth" not in text.lower()
        assert case["folklore_line"] not in text


def test_quiz_has_every_case_and_no_answers(cases):
    from memtrap.quiz import render_quiz

    text = render_quiz(cases)
    active = [c for c in cases if c["status"] != "reserve"]
    assert text.count("## Case ") == len(active) == 17
    for case in active:
        assert case["folklore_line"] not in text
        assert case["why_it_matters"] not in text
    assert "correct" not in text.lower()
