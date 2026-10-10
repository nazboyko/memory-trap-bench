import ast
import copy

import pytest

from memtrap.build_tasks import OUT_DIR, TASKS, BuildError, build_items, module_source, render_task
from memtrap.catalog import ROOT, by_id, load_truth
from tests.conftest import N_CASES


@pytest.fixture
def truth():
    return copy.deepcopy(load_truth())


def test_item_counts(cases, truth):
    assert len(build_items(cases, truth, "cold")) == N_CASES == 17
    split = sum(1 for c in cases if c["status"] == "measured" and truth[c["id"]]["version_split"])
    assert len(build_items(cases, truth, "versioned")) == N_CASES + split
    assert len(build_items(cases, truth, "evidence")) == N_CASES + split


def test_split_case_is_asked_per_version(cases, truth):
    items = build_items(cases, truth, "versioned")
    assert {"P1@php-8.1", "P1@php-8.3"} <= set(items)
    assert items["P1@php-8.1"]["truth"]["versions"]["php-8.1"]["answer"] != items["P1@php-8.3"]["truth"]["versions"]["php-8.3"]["answer"]
    assert "P2@php-8.3" in items and "P2@php-8.1" not in items


def test_evidence_uses_source_case_count(cases, truth):
    items = build_items(cases, truth, "evidence")
    # R1 takes its fact from R3, which runs 100 goroutines, not 100,000.
    assert "2,048.0 KB" in items["R1@go-1.24"]["prompt"]


def test_draft_case_is_refused(cases, truth):
    by_id(cases)["P2"]["status"] = "draft"
    with pytest.raises(BuildError):
        build_items(cases, truth, "cold")


def test_missing_truth_is_refused(cases, truth):
    del truth["G3"]["versions"]["go-1.23"]
    with pytest.raises(BuildError):
        build_items(cases, truth, "versioned")


def test_incomplete_truth_is_refused(cases, truth):
    truth["R2"]["complete"] = False
    with pytest.raises(BuildError):
        build_items(cases, truth, "cold")


def test_rendered_task_is_valid_python_with_cells(cases, truth):
    items = build_items(cases, truth, "cold")
    src = render_task(TASKS["cold"], "cold", items)
    ast.parse(src)
    assert src.count("# %%") >= 5
    assert "import kaggle_benchmarks as kbench" in src
    assert "@kbench.task(name='MemoryTrap Cold'" in src
    assert src.rstrip().endswith("# %choose memtrap_cold")
    assert "memtrap_cold.run(kbench.llm)" in src
    assert "from __future__" not in src


def test_task_text_fits_the_kaggle_limit(cases, truth):
    from memtrap.build_tasks import KAGGLE_TEXT_LIMIT, PILOT

    items = build_items(cases, truth, "cold")
    for spec in [*TASKS.values(), PILOT]:
        assert len(spec["name"]) <= KAGGLE_TEXT_LIMIT and len(spec["summary"]) <= KAGGLE_TEXT_LIMIT, spec["file"]
    with pytest.raises(BuildError):
        render_task({**TASKS["cold"], "summary": "x" * (KAGGLE_TEXT_LIMIT + 1)}, "cold", items)


def test_module_source_drops_docstring():
    src = module_source(ROOT / "memtrap" / "parse.py")
    assert src.startswith("import json")


def test_stub_models_score_as_expected(tmp_path, monkeypatch):
    from memtrap.build_tasks import OUT_DIR
    from memtrap.stub_run import check, load_task, run_stub

    monkeypatch.chdir(tmp_path)
    spec = TASKS["cold"]
    namespace = load_task(OUT_DIR / spec["file"], spec["function"])
    for mode in ("correct", "folklore", "garbage"):
        assert check(mode, run_stub(namespace, spec["function"], mode)) == []


def test_committed_task_files_are_current(cases, truth):
    from memtrap.build_tasks import PILOT

    for cond, spec in TASKS.items():
        expected = render_task(spec, cond, build_items(cases, truth, cond))
        assert (OUT_DIR / spec["file"]).read_text() == expected, f"rebuild {spec['file']}"
    pilot = render_task(PILOT, "cold", build_items(cases, truth, "cold"), 3)
    assert (OUT_DIR / PILOT["file"]).read_text() == pilot


def test_repair_turn_and_final_json_are_scored(tmp_path, monkeypatch):
    from memtrap.stub_run import load_task, run_stub

    monkeypatch.chdir(tmp_path)
    spec = TASKS["cold"]
    namespace = load_task(OUT_DIR / spec["file"], spec["function"])
    for mode in ("repair", "draft"):
        s = run_stub(namespace, spec["function"], mode)
        assert s["task_score"] == 1.0 and s["parse_failures"] == 0, mode


def failing_stub_run(tmp_path, monkeypatch, mode, refusals=2):
    from memtrap.stub_run import StubLLM, load_task

    monkeypatch.chdir(tmp_path)
    spec = TASKS["cold"]
    namespace = load_task(OUT_DIR / spec["file"], spec["function"])
    namespace["QUOTA_SLEEP"] = namespace["RETRY_SLEEP"] = 0
    stub = StubLLM(mode, namespace["ITEMS"])
    stub.refusals = refusals
    run = namespace[spec["function"]].run(stub)
    return namespace, stub, run


def test_quota_refusals_are_waited_out(tmp_path, monkeypatch):
    # More refusals than ATTEMPTS: only the wait path can get a reply.
    namespace, stub, run = failing_stub_run(tmp_path, monkeypatch, "quota", refusals=5)
    assert namespace["ATTEMPTS"] < 5 < namespace["QUOTA_WAITS"]
    assert run.result == 1.0
    assert set(stub.calls.values()) == {6}  # five refusals, then the reply
    assert set(stub.caps) == {namespace["MAX_OUTPUT_TOKENS"]}
    # A retry asks for a reply to the same history: the question is in it once.
    assert set(stub.last_messages) == set(stub.by_prompt)
    assert set(stub.history_lengths) == {1}


def test_a_spent_quota_gives_up_and_keeps_the_cap(tmp_path, monkeypatch):
    namespace, stub, run = failing_stub_run(tmp_path, monkeypatch, "quota", refusals=None)
    assert run.result == 0.0
    assert set(stub.calls.values()) == {namespace["QUOTA_WAITS"] + namespace["ATTEMPTS"]}
    assert stub.name not in namespace["UNCAPPED"]


def test_a_refused_output_cap_is_dropped_for_that_model(tmp_path, monkeypatch):
    namespace, stub, run = failing_stub_run(tmp_path, monkeypatch, "badcap")
    assert run.result == 1.0
    assert stub.name in namespace["UNCAPPED"]
    assert stub.caps.count(None) >= len(namespace["ITEMS"])
    assert set(stub.history_lengths) == {1}


def test_a_dead_proxy_gives_up_after_three_errors(tmp_path, monkeypatch):
    namespace, stub, run = failing_stub_run(tmp_path, monkeypatch, "down")
    assert run.result == 0.0
    assert set(stub.calls.values()) == {namespace["ATTEMPTS"]}
    assert not [sub for sub in run.subruns if isinstance(sub.result, dict)]
