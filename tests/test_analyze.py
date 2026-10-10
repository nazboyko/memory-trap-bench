import json

import pytest

from memtrap.analyze import build_report, choose_runs, load_runs, score_run, stability, task_score
from memtrap.build_tasks import OUT_DIR, TASKS, build_items
from memtrap.catalog import ROOT, load_cases, load_truth
from memtrap.stub_run import load_task, run_stub
from tests.conftest import N_CASES


@pytest.fixture(scope="module")
def stub_runs(tmp_path_factory):
    """Run files written by the real kbench library for the three stubs."""
    import os

    root = tmp_path_factory.mktemp("runs")
    old = os.getcwd()
    try:
        for cond, spec in TASKS.items():
            d = root / cond
            d.mkdir()
            os.chdir(d)
            ns = load_task(OUT_DIR / spec["file"], spec["function"])
            for mode in ("correct", "folklore", "garbage"):
                run_stub(ns, spec["function"], mode)
    finally:
        os.chdir(old)
    return root


def test_task_score_reads_dropped_zero():
    assert task_score({"results": [{"type": "AGGREGATED", "numericResult": {}}]}) == 0.0
    assert task_score({"results": [{"numericResult": {"value": 0.5}}]}) == 0.5


def newest_of(stub_runs):
    cases, truth = load_cases(), load_truth()
    return choose_runs(load_runs(stub_runs), cases, truth)


def test_rescore_matches_stored(stub_runs):
    cases, truth = load_cases(), load_truth()
    entries = load_runs(stub_runs)
    newest = choose_runs(entries, cases, truth)
    assert len(newest) == 9 and len(entries) == 9
    for entry in newest.values():
        rows, meta = score_run(entry, cases, truth)
        assert meta["mismatches"] == 0 and meta["errored_items"] == 0
        assert entry["complete"] and entry["model"].startswith("stub-")


def test_missing_items_count_as_errored(stub_runs, tmp_path):
    cases, truth = load_cases(), load_truth()
    newest = newest_of(stub_runs)
    entry = newest[("cold", "stub-correct")]
    data = json.loads(json.dumps(entry["data"]))
    data["subruns"] = data["subruns"][:-2]
    rows, meta = score_run({**entry, "data": data}, cases, truth)
    assert meta["errored_items"] == 2
    assert sum(r["case_score"] for r in rows) == N_CASES - 2


def test_report_sections_and_controls(stub_runs, tmp_path):
    cases, truth = load_cases(), load_truth()
    text = build_report(cases, truth, load_runs(stub_runs), stub_runs, tmp_path, "STUB DATA")
    for heading in ("## 1.", "## 2. Leaderboard", "## 4. Right answer", "## 5. Confidence",
                    "## 6. Version sensitivity", "## 7. Evidence update", "## 8. Folklore index",
                    "## 9. Controls", "## 11. Folklore in the models' own words",
                    "## 12. The same question twice", "## 13. Limits"):
        assert heading in text
    assert "| `stub-correct` | 1.00 | 1.00 | 1.00 | 1.00 | 0 |" in text
    total = sum(len(build_items(cases, truth, c)) for c in ("cold", "versioned", "evidence"))
    assert f"| `stub-garbage` | 0.00 | 0.00 | 0.00 | 0.00 | {total} |" in text
    assert "Every model and condition pair has a complete run." in text
    for cid in ("G4", "R1", "R4"):
        assert f"| {cid} | control | n/a | n/a |" in text
    for name in ("split_cases.png", "leaderboard.png", "heatmap_cold.png", "mechanism.png", "confidence.png"):
        assert (tmp_path / "charts" / name).exists()


def test_report_without_runs_has_no_model_numbers(tmp_path):
    cases, truth = load_cases(), load_truth()
    text = build_report(cases, truth, [], tmp_path / "none", tmp_path, None)
    assert "Pending: there are no Kaggle run files" in text
    assert "## 2. Leaderboard" not in text


def test_report_never_reads_human_files():
    src = (ROOT / "memtrap" / "analyze.py").read_text()
    assert "human/answers" not in src.replace("memtrap.score_human human/answers.json", "")


def test_errored_items_are_not_parse_failures(stub_runs):
    from memtrap.score import summarize

    cases, truth = load_cases(), load_truth()
    newest = newest_of(stub_runs)
    entry = newest[("cold", "stub-folklore")]
    data = json.loads(json.dumps(entry["data"]))
    data["subruns"] = data["subruns"][:-2]
    rows, meta = score_run({**entry, "data": data}, cases, truth)
    s = summarize(rows)
    assert meta["errored_items"] == 2 and s["parse_failures"] == 0
    assert sum(r["errored"] for r in rows) == 2


def test_stale_prompt_is_not_scored(stub_runs):
    cases, truth = load_cases(), load_truth()
    newest = newest_of(stub_runs)
    entry = newest[("cold", "stub-correct")]
    data = json.loads(json.dumps(entry["data"]))
    first = data["subruns"][0]["conversations"][0]["requests"][0]["contents"][0]
    first["parts"][0]["text"] = "an older prompt"
    rows, meta = score_run({**entry, "data": data}, cases, truth)
    assert meta["stale_items"] == 1 and meta["errored_items"] == 1


def kaggle_layout(stub_runs, root, version, mode, as_model, drop=0, cond="cold"):
    """Copy one stub run file into the layout `kaggle b t download` writes."""
    src = next(p for p in (stub_runs / cond).glob("*.run.json") if f"stub-{mode}" in p.name)
    data = json.loads(src.read_text())
    if drop:
        data["subruns"] = data["subruns"][:-drop]
    target = root / cond / f"memorytrap-{cond}" / str(version) / as_model / "100"
    target.mkdir(parents=True)
    (target / src.name).write_text(json.dumps(data))


def test_model_name_and_version_come_from_the_download_layout(stub_runs, tmp_path):
    kaggle_layout(stub_runs, tmp_path, 2, "correct", "some-model-default")
    (entry,) = load_runs(tmp_path)
    assert (entry["model"], entry["version"], entry["condition"]) == ("some-model-default", 2, "cold")


def test_a_complete_run_beats_a_newer_incomplete_one(stub_runs, tmp_path):
    cases, truth = load_cases(), load_truth()
    kaggle_layout(stub_runs, tmp_path, 1, "correct", "m")
    kaggle_layout(stub_runs, tmp_path, 2, "folklore", "m", drop=3)
    chosen = choose_runs(load_runs(tmp_path), cases, truth)
    assert chosen[("cold", "m")]["version"] == 1 and chosen[("cold", "m")]["complete"]


def test_the_newest_version_wins_among_complete_runs(stub_runs, tmp_path):
    cases, truth = load_cases(), load_truth()
    # The version 2 file is the older one (the fixture runs "correct" first),
    # so picking by end time alone would choose version 1.
    kaggle_layout(stub_runs, tmp_path, 2, "correct", "m")
    kaggle_layout(stub_runs, tmp_path, 1, "folklore", "m")
    entries = load_runs(tmp_path)
    chosen = choose_runs(entries, cases, truth)
    assert chosen[("cold", "m")]["version"] == 2
    by_version = {e["version"]: e["end"] for e in entries}
    assert by_version[2] < by_version[1]
    (line,) = stability(entries, chosen)
    assert line["items"] == N_CASES and line["runs"] == 1
    # The correct and the folklore stub agree only where the rule of thumb holds.
    controls = sum(1 for c in cases if c.get("control") and c["status"] == "measured" and not c.get("reserve"))
    assert line["same_answer"] >= controls and line["same_answer"] < N_CASES


def test_an_incomplete_run_is_listed_and_not_ranked(stub_runs, tmp_path):
    cases, truth = load_cases(), load_truth()
    kaggle_layout(stub_runs, tmp_path, 2, "correct", "whole")
    kaggle_layout(stub_runs, tmp_path, 2, "correct", "holes", drop=4)
    out = tmp_path / "out"
    text = build_report(cases, truth, load_runs(tmp_path), tmp_path, out, None)
    assert "| `whole` | 1.00 | n/a | n/a | n/a | 0 |" in text
    assert text.count("`holes`") == 1
    assert f"| `holes` | cold | 2 | {N_CASES - 4} of {N_CASES} | 0 | 0.000 | 0 |" in text


def test_versioned_to_evidence_is_not_zero_without_a_versioned_run(stub_runs, tmp_path):
    cases, truth = load_cases(), load_truth()
    kaggle_layout(stub_runs, tmp_path, 2, "folklore", "m", cond="cold")
    kaggle_layout(stub_runs, tmp_path, 2, "correct", "m", cond="evidence")
    kaggle_layout(stub_runs, tmp_path, 2, "correct", "m", cond="versioned", drop=2)
    text = build_report(cases, truth, load_runs(tmp_path), tmp_path, tmp_path / "out", None)
    section = text.split("## 7. Evidence update")[1].split("## 8.")[0]
    row = next(line for line in section.splitlines() if line.startswith("| `m` |"))
    assert row.endswith("| n/a | n/a |") and "| 0 | 0 |" not in row


def test_usage_reads_cost_and_the_longest_single_reply():
    from memtrap.analyze import usage

    item = {"conversations": [{
        "metrics": {"outputTokens": 300, "inputTokensCostNanodollars": "1000000", "outputTokensCostNanodollars": "2000000"},
        "requests": [{"metrics": {"outputTokens": 100}}, {"metrics": {"outputTokens": 200}}],
    }]}
    assert usage(item) == {"tokens": 300, "cost": 0.003, "longest": 200}
    assert usage({"conversations": [{"metrics": {"outputTokens": 50}}]})["longest"] == 50


def test_pair_counts_and_control_quotes(stub_runs):
    from memtrap.analyze import PAIR, control_quote_rows, pair_counts

    cases, truth = load_cases(), load_truth()
    chosen = newest_of(stub_runs)
    rows_by = {key: e["rows"] for key, e in chosen.items()}
    counts, both = pair_counts(rows_by)
    # The correct stub gets both; the folklore stub keeps the control and loses the other.
    assert counts["cold"][(True, True)] == 1 and counts["cold"][(True, False)] == 1
    assert "`stub-correct` (cold)" in both and len(both) == 3
    rows_all = {"stub-garbage": [r for (c, m), rows in rows_by.items() if m == "stub-garbage" for r in rows],
                "stub-correct": [r for (c, m), rows in rows_by.items() if m == "stub-correct" for r in rows]}
    # The garbage stub gives no reasoning and the correct stub loses no control: nothing to quote.
    assert control_quote_rows(rows_all, ["stub-correct", "stub-garbage"], cases) == []
    assert PAIR == ("R4", "R6")


def test_quotes_file_is_verbatim_and_short_first(stub_runs, tmp_path):
    from memtrap.analyze import quotes_file

    cases, truth = load_cases(), load_truth()
    row = {"case": "G3", "condition": "cold", "answer": "B", "answer_ok": 0, "confidence": 90, "folklore_mechanism": 1}
    rows_all = {"m-long": [{**row, "reasoning": "struct{} is free,   so the set is half the size of the bool map."}],
                "m-short": [{**row, "reasoning": "struct{} is free."}],
                "m-right": [{**row, "folklore_mechanism": 0, "reasoning": "Same slot size."}]}
    text = quotes_file(rows_all, ["m-long", "m-short", "m-right"], cases, truth)
    g3 = text.split("## G3")[1].split("## ")[0]
    assert g3.index("`m-short`") < g3.index("`m-long`") and "m-right" not in g3
    assert "> struct{} is free, so the set is half the size of the bool map." in g3
    assert "No scored reply chose the folklore mechanism." in text.split("## P2")[1].split("## ")[0]
    # The report writes the file next to itself.
    build_report(cases, truth, load_runs(stub_runs), stub_runs, tmp_path, None)
    assert (tmp_path / "quotes.md").exists()


def test_baseline_file_is_consistent_and_reported(stub_runs, tmp_path):
    from memtrap.analyze import BASELINE_FILE, load_baseline

    real = load_baseline(ROOT / "results")
    assert real is not None and real["condition"] == "cold" and real["cases"] == N_CASES
    assert round(real["computed_score"], 2) == real["score"]
    assert load_baseline(tmp_path) is None
    cases, truth = load_cases(), load_truth()
    plain = build_report(cases, truth, load_runs(stub_runs), stub_runs, tmp_path, None)
    assert "is not read by this report" in plain.split("## 10. Human baseline")[1].split("## 11.")[0]
    (tmp_path / BASELINE_FILE).write_text((ROOT / "results" / BASELINE_FILE).read_text())
    text = build_report(cases, truth, load_runs(stub_runs), stub_runs, tmp_path, None)
    section = text.split("## 10. Human baseline")[1].split("## 11.")[0]
    n = real["cases"]
    assert f"| author | {real['score']:.2f} | {real['answers_right']} of {n} | {real['mechanisms_right']} of {n} |" in section
    # The correct stub is above the author, the garbage stub below.
    assert "next above: `stub-correct` 1.00" in section and "next below:" in section
