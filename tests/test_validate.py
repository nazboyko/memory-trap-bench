from memtrap.catalog import by_id
from memtrap.validate import go_program, validate
from tests.conftest import letter_of


def errors_for(write_catalog, cases, truth=None):
    cpath, tpath = write_catalog(cases, truth)
    errs, _ = validate(cpath, tpath, check_syntax=False)
    return errs


def test_real_catalog_is_valid():
    errs, _ = validate()
    assert errs == []


def test_real_catalog_has_fifteen_cases_and_three_controls(cases):
    active = [c for c in cases if c["status"] != "reserve"]
    assert len(active) == 15
    assert sum(c["control"] for c in active) == 3


def test_duplicate_id_fails(write_catalog, cases):
    cases.append(dict(cases[0]))
    assert any("duplicate" in e for e in errors_for(write_catalog, cases))


def test_missing_field_fails(write_catalog, cases):
    del cases[0]["question"]
    assert any("missing field question" in e for e in errors_for(write_catalog, cases))


def test_control_with_different_folklore_mechanism_fails(write_catalog, cases):
    control = next(c for c in cases if c["control"])
    other = next(k for k in "ABCD" if k != control["correct_mechanism"])
    control["folklore_mechanism"] = other
    assert any("control case" in e for e in errors_for(write_catalog, cases))


def test_non_control_with_folklore_equal_to_correct_fails(write_catalog, cases):
    case = next(c for c in cases if not c["control"])
    case["folklore_mechanism"] = case["correct_mechanism"]
    assert any("must differ" in e for e in errors_for(write_catalog, cases))


def test_longest_correct_mechanism_fails(write_catalog, cases):
    case = next(c for c in cases if not c["control"])
    case["mechanisms"][case["correct_mechanism"]] += " " + "x" * 300
    assert any("gives the answer away" in e for e in errors_for(write_catalog, cases))


def test_three_options_fail(write_catalog, cases):
    del cases[0]["options"]["D"]
    assert any("exactly the keys" in e for e in errors_for(write_catalog, cases))


def test_unknown_name_in_quantity_fails(write_catalog, cases):
    cases[0]["decision"]["quantities"] = {"r": "zz / a"}
    assert any("unknown names" in e for e in errors_for(write_catalog, cases))


def test_rule_for_version_option_fails(write_catalog, cases):
    case = next(c for c in cases if c["version_option"])
    case["decision"]["rules"].append({"option": case["version_option"], "when": {"r": [5, 6]}})
    assert any("version option cannot be decided" in e for e in errors_for(write_catalog, cases))


def test_evidence_from_missing_case_fails(write_catalog, cases):
    cases[0]["evidence"]["from"] = "Z9"
    assert any("does not exist" in e for e in errors_for(write_catalog, cases))


def test_evidence_placeholder_must_name_a_variant(write_catalog, cases):
    cases[0]["evidence"]["text"] = "Measured {zz_MB} MB."
    assert any("names no variant" in e for e in errors_for(write_catalog, cases))


def test_banned_word_in_question_fails(write_catalog, cases):
    cases[0]["question"] = "What actually happens to memory?"
    assert any("actually" in e for e in errors_for(write_catalog, cases))


def test_two_controls_fail(write_catalog, cases):
    control = next(c for c in cases if c["control"] and c["status"] != "reserve")
    control["status"] = "reserve"
    assert any("three control cases" in e for e in errors_for(write_catalog, cases))


def truth_for(case, answers):
    return {
        case["id"]: {
            "versions": {v: {"answer": a, "estimate": 1.0} for v, a in answers.items()},
        }
    }


def test_measured_case_without_truth_fails(write_catalog, cases):
    cases[0]["status"] = "measured"
    assert any("missing from truth.json" in e for e in errors_for(write_catalog, cases, {}))


def test_split_without_version_option_fails(write_catalog, cases):
    case = next(c for c in cases if c["version_option"] is None and not c["control"])
    case["versions"] = [f"{case['runtime']}-x", f"{case['runtime']}-y"]
    case["status"] = "measured"
    truth = truth_for(case, {case["versions"][0]: "A", case["versions"][1]: "B"})
    assert any("no version option" in e for e in errors_for(write_catalog, cases, truth))


def test_unresolved_measurement_fails(write_catalog, cases):
    case = cases[0]
    case["status"] = "measured"
    truth = truth_for(case, {v: None for v in case["versions"]})
    assert any("matches no option" in e for e in errors_for(write_catalog, cases, truth))


def test_control_whose_folklore_is_wrong_fails(write_catalog, cases):
    case = next(c for c in cases if c["control"])
    case["status"] = "measured"
    wrong = next(k for k in "ABCD" if k != case["folklore_answer"])
    truth = truth_for(case, {v: wrong for v in case["versions"]})
    assert any("control case, but" in e for e in errors_for(write_catalog, cases, truth))


def test_go_program_splits_top_level_and_main():
    src = go_program("var x int\n\nfunc f() {}\n\n// in main:\nf()\n_ = x")
    assert src.index("func f()") < src.index("func main()") < src.index("f()\n_ = x")


def test_every_case_evidence_comes_from_same_runtime(cases):
    index = by_id(cases)
    for c in cases:
        assert index[c["evidence"]["from"]]["runtime"] == c["runtime"]


def test_overlapping_rules_fail(write_catalog, cases):
    p1 = by_id(cases)["P1"]
    lo, hi = p1["decision"]["rules"][0]["when"]["r"]
    p1["decision"]["rules"][1]["when"]["r"] = [lo + (hi - lo) / 2, hi + 1]
    assert any("overlap" in e for e in errors_for(write_catalog, cases))


def test_unsupported_operator_fails(write_catalog, cases):
    cases[0]["decision"]["quantities"] = {"r": "b ** a"}
    assert any("unsupported expression" in e for e in errors_for(write_catalog, cases))


def test_bad_evidence_format_fails(write_catalog, cases):
    cases[0]["evidence"]["text"] = "Measured {} MB."
    assert errors_for(write_catalog, cases)


def test_reserved_variant_name_fails(write_catalog, cases):
    cases[0]["variants"]["n"] = "a clash"
    assert any("reserved" in e for e in errors_for(write_catalog, cases))


def test_folklore_cannot_be_the_version_option(write_catalog, cases):
    case = next(c for c in cases if c["version_option"])
    case["folklore_answer"] = case["version_option"]
    assert any("folklore answer cannot be the version option" in e for e in errors_for(write_catalog, cases))


def test_shortest_correct_mechanism_fails(write_catalog, cases):
    case = next(c for c in cases if not c["control"])
    case["mechanisms"][case["correct_mechanism"]] = "Short."
    assert any("shortest" in e for e in errors_for(write_catalog, cases))


def test_wrong_cold_answer_in_truth_fails(write_catalog, cases):
    case = next(c for c in cases if c["version_option"])
    case["status"] = "measured"
    rules = [r["option"] for r in case["decision"]["rules"]]
    truth = truth_for(case, {case["versions"][0]: rules[0], case["versions"][1]: rules[1]})
    truth[case["id"]]["cold_answer"] = rules[0]
    truth[case["evidence"]["from"]] = {"versions": {v: {} for v in case["versions"]}}
    assert any("cold answer" in e for e in errors_for(write_catalog, cases, truth))


def test_stale_folklore_on_older_version_is_allowed(write_catalog, cases):
    p4 = by_id(cases)["P4"]
    p4["status"] = "measured"
    other = letter_of(p4, "About the same")
    truth = truth_for(p4, {"php-8.1": p4["folklore_answer"], "php-8.3": other})
    truth["P4"]["cold_answer"] = p4["version_option"]
    truth[p4["evidence"]["from"]] = {"versions": {v: {} for v in p4["versions"]}}
    errs = [e for e in errors_for(write_catalog, cases, truth) if e.startswith("P4")]
    assert errs == []


def test_evidence_source_needs_truth_for_each_version(write_catalog, cases):
    p4 = by_id(cases)["P4"]
    p4["status"] = "measured"
    other = next(r["option"] for r in p4["decision"]["rules"] if r["option"] != p4["folklore_answer"])
    truth = truth_for(p4, {"php-8.1": p4["folklore_answer"], "php-8.3": other})
    truth["P4"]["cold_answer"] = p4["version_option"]
    assert any("evidence source" in e for e in errors_for(write_catalog, cases, truth))


def test_evidence_echo_inside_tolerance_fails(write_catalog, cases):
    from memtrap.catalog import load_truth

    truth = load_truth()
    index = by_id(cases)
    g3 = index["G3"]
    g3["evidence"] = {"from": "G1", "text": "A map[int64]int64 measured {a_MB} MB."}
    errs = errors_for(write_catalog, cases, truth)
    assert any(e.startswith("G3") and "copying it would score" in e for e in errs)
