from memtrap.catalog import by_id
from memtrap.validate import go_program, validate


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
