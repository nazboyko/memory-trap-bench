import pytest

from memtrap.catalog import by_id, decide, estimate_value, eval_expr, render_evidence
from tests.conftest import letter_of


def test_eval_expr_arithmetic():
    assert eval_expr("b / a", {"a": 2, "b": 5}) == 2.5
    assert eval_expr("a / n / KB", {"a": 2048 * 10, "n": 10, "KB": 1024}) == 2.0
    assert eval_expr("-a + 1", {"a": 3}) == -2


@pytest.mark.parametrize("expr", ["__import__('os')", "a ** 2", "a.b", "[a]"])
def test_eval_expr_rejects_anything_else(expr):
    with pytest.raises((ValueError, KeyError)):
        eval_expr(expr, {"a": 1})


def test_eval_expr_unknown_name():
    with pytest.raises(KeyError):
        eval_expr("b / a", {"a": 1})


def test_decide_picks_matching_rule(cases):
    p1 = by_id(cases)["P1"]
    mb = 1 << 20
    assert decide(p1, {"a": 16 * mb, "b": 40 * mb}, 1_000_000) == letter_of(p1, "$b takes about 2.5x")
    assert decide(p1, {"a": 32 * mb, "b": 40 * mb}, 1_000_000) == letter_of(p1, "$b takes about 25%")
    assert decide(p1, {"a": 40 * mb, "b": 40 * mb}, 1_000_000) == letter_of(p1, "About the same")


def test_decide_returns_none_between_options(cases):
    p1 = by_id(cases)["P1"]
    assert decide(p1, {"a": 10, "b": 18}, 1) is None


def test_decide_two_quantities(cases):
    p5 = by_id(cases)["P5"]
    mb = 1 << 20
    assert decide(p5, {"a": 16 * mb, "b": 0, "c": 16 * mb}, 1_000_000) == letter_of(p5, "Line 2 adds almost nothing")
    assert decide(p5, {"a": 16 * mb, "b": 16 * mb, "c": 0}, 1_000_000) == letter_of(p5, "Line 2 adds a full copy")


def test_decide_upper_bound_is_exclusive(cases):
    p1 = by_id(cases)["P1"]
    assert decide(p1, {"a": 10, "b": 11}, 1) == letter_of(p1, "$b takes about 25%")
    assert decide(p1, {"a": 10, "b": 10.999}, 1) == letter_of(p1, "About the same")


def test_decide_zero_denominator_is_unresolved(cases):
    index = by_id(cases)
    assert decide(index["P1"], {"a": 0, "b": 40}, 1) is None
    assert decide(index["R3"], {"a": 100 * 600 * 1024, "peak": 0, "gc1": 0, "gc4": 0}, 100) is None


def test_estimate_value_units(cases):
    index = by_id(cases)
    assert estimate_value(index["P1"], {"a": 1, "b": 40 << 20}, 1) == 40.0
    assert estimate_value(index["R1"], {"a": 2048 * 100_000}, 100_000) == 2.0


def test_render_evidence():
    text = "took {a_MB} MB, {peak_KB_each} KB each, {b_KB} KB"
    out = render_evidence(text, {"a": 3 << 20, "peak": 2048 * 4, "b": 1536}, 4)
    assert out == "took 3.0 MB, 2.0 KB each, 1.5 KB"
