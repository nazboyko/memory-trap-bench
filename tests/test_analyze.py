import json

import pytest

from memtrap.analyze import build_report, load_runs, score_run, task_score
from memtrap.build_tasks import OUT_DIR, TASKS
from memtrap.catalog import ROOT, load_cases, load_truth
from memtrap.stub_run import load_task, run_stub


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


def test_rescore_matches_stored(stub_runs):
    cases, truth = load_cases(), load_truth()
    newest, seen = load_runs(stub_runs)
    assert len(newest) == 9 and len(seen) == 9
    for entry in newest.values():
        rows, meta = score_run(entry, cases, truth)
        assert meta["mismatches"] == 0 and meta["errored_items"] == 0


def test_missing_items_count_as_errored(stub_runs, tmp_path):
    cases, truth = load_cases(), load_truth()
    newest, _ = load_runs(stub_runs)
    entry = newest[("cold", "stub-correct")]
    data = json.loads(json.dumps(entry["data"]))
    data["subruns"] = data["subruns"][:-2]
    rows, meta = score_run({**entry, "data": data}, cases, truth)
    assert meta["errored_items"] == 2
    assert sum(r["case_score"] for r in rows) == 13


def test_report_sections_and_controls(stub_runs, tmp_path):
    cases, truth = load_cases(), load_truth()
    newest, seen = load_runs(stub_runs)
    text = build_report(cases, truth, newest, seen, stub_runs, tmp_path, "STUB DATA")
    for heading in ("## 1.", "## 2. Leaderboard", "## 4. Right answer", "## 5. Confidence",
                    "## 6. Version sensitivity", "## 7. Evidence update", "## 8. Folklore index",
                    "## 9. Controls", "## 11. Folklore in the models' own words"):
        assert heading in text
    assert "| `stub-correct` | 1.00 | 1.00 | 1.00 | 1.00 | 0 |" in text
    assert "| `stub-garbage` | 0.00 | 0.00 | 0.00 | 0.00 | 51 |" in text
    for cid in ("G4", "R1", "R4"):
        assert f"| {cid} | control | n/a | n/a |" in text
    for name in ("split_cases.png", "leaderboard.png", "heatmap_cold.png", "mechanism.png", "confidence.png"):
        assert (tmp_path / "charts" / name).exists()


def test_report_without_runs_has_no_model_numbers(tmp_path):
    cases, truth = load_cases(), load_truth()
    text = build_report(cases, truth, {}, [], tmp_path / "none", tmp_path, None)
    assert "Pending: there are no Kaggle run files" in text
    assert "## 2. Leaderboard" not in text


def test_report_never_reads_human_files():
    src = (ROOT / "memtrap" / "analyze.py").read_text()
    assert "human/answers" not in src.replace("memtrap.score_human human/answers.json", "")
