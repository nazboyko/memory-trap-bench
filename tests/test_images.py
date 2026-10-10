import copy
import json

import pytest
from PIL import Image

from memtrap.catalog import ROOT, active_cases, load_cases, load_truth, version_label
from memtrap.images import (
    ImageError,
    baseline_label,
    card_facts,
    collect,
    control_pair,
    main,
    short_versions,
    write_all,
)
from memtrap.prompts import CONDITIONS
from tests.conftest import N_CASES


@pytest.fixture(scope="module")
def data():
    return collect()


def test_board_is_sorted_and_complete(data):
    board = data["board"]
    assert len(board) >= 11
    means = [row["mean"] for row in board]
    assert means == sorted(means, reverse=True)
    for row in board:
        assert abs(row["mean"] - (row["cold"] + row["versioned"] + row["evidence"]) / 3) < 1e-9
    assert data["cases"] == N_CASES and data["models"] == len(board)
    # Counted a second way: the runtime versions the Actions matrix wrote readings for.
    measured = {f.name.removeprefix("gha-").rsplit("-", 1)[0] for f in (ROOT / "results" / "measurements").glob("gha-*.json")}
    assert data["runtime_versions"] == len(measured)


def test_control_pair_comes_from_the_runs_and_the_truth(data):
    control = data["control"]
    truth = load_truth()[control["id"]]
    assert control["id"] == "R4" and control["metric"] == truth["metric"]
    assert [m["model"] for m in control["said"]] == [row["model"] for row in data["board"][:2]]
    assert [m["version"] for m in control["measured"]] == sorted(version_label(v) for v in truth["versions"])
    pair = control_pair(data)
    assert {pair["measured"]} == {e["estimate"] for e in truth["versions"].values()}
    assert pair["unit"] == "KB" and pair["said"] != pair["measured"]
    for model in control["said"]:
        assert len(model["confidences"]) == len(CONDITIONS) and not any(model["right"])
    facts = card_facts(data)
    assert facts["held_kb"] == 8 and {facts["n"]} == {e["n"] for e in truth["versions"].values()}


@pytest.mark.parametrize("change", [
    lambda d: d["control"]["said"][0]["right"].__setitem__(0, True),           # a top model gets the answer right
    lambda d: d["control"]["said"][1]["estimate_right"].__setitem__(2, True),  # or the estimate
    lambda d: d["control"]["said"][0]["estimates"].__setitem__(1, 8.0),        # the two no longer agree
    lambda d: d["control"]["said"][0]["answers"].__setitem__(1, "C"),
    lambda d: d["control"]["said"][1]["confidences"].__setitem__(0, None),     # a reply without a confidence
    lambda d: d["control"]["said"][0]["estimates"].__setitem__(0, None),
    lambda d: d["control"]["said"][0]["answers"].pop(),                        # not one reply per task
    lambda d: d["control"]["measured"][0].__setitem__("value", 16.0),          # versions measure differently
    lambda d: d["board"][2].__setitem__("mean", d["board"][1]["mean"]),        # no clear second place
])
def test_cover_and_card_are_refused_when_the_data_stops_supporting_them(data, change):
    changed = copy.deepcopy(data)
    change(changed)
    with pytest.raises(ImageError):
        control_pair(changed)


@pytest.mark.parametrize("change", [
    lambda d: d["control"].__setitem__("snippet", d["control"]["snippet"].replace("[8192]byte", "[]byte")),
    lambda d: d["control"].__setitem__("snippet", d["control"]["snippet"].replace("hold8k", "hold")),
    lambda d: d["control"]["measured"][1].__setitem__("readings", 5),
])
def test_card_is_refused_when_the_case_no_longer_fits_its_shortened_code(data, change):
    changed = copy.deepcopy(data)
    change(changed)
    with pytest.raises(ImageError):
        card_facts(changed)


def test_baseline_label_and_short_versions():
    assert baseline_label({"computed_score": 34 / 51}) == "author, cold only: 0.67"
    assert short_versions(["Go 1.23", "Go 1.24", "Go 1.25"]) == "Go 1.23, 1.24, 1.25"
    assert short_versions(["PHP 8.1", "Go 1.24"]) == "PHP 8.1, Go 1.24"


def test_images_have_the_sizes_the_post_needs(tmp_path):
    cover, board, card = write_all(tmp_path)
    with Image.open(cover) as im:
        assert im.size == (1000, 420) and im.mode == "RGB"
    with Image.open(board) as im:
        assert im.size[0] == 1600
    with Image.open(card) as im:
        assert im.size == (1200, 675)


def test_chart_is_drawn_without_a_baseline(tmp_path):
    paths = write_all(tmp_path / "plain", results_dir=tmp_path)  # no baseline file in that directory
    assert all(path.exists() for path in paths)


def test_a_refusal_keeps_the_chart_and_writes_no_cover_or_card(tmp_path, monkeypatch):
    import memtrap.images as images

    def refuse(data):
        raise ImageError("refused")

    monkeypatch.setattr(images, "control_pair", refuse)
    with pytest.raises(ImageError):
        images.write_all(tmp_path)
    assert (tmp_path / "leaderboard.png").exists()
    assert not (tmp_path / "cover.png").exists() and not (tmp_path / "card-goroutine.png").exists()


def test_main_reports_a_refusal(monkeypatch, capsys):
    import memtrap.images as images

    monkeypatch.setattr(images, "write_all", lambda: (_ for _ in ()).throw(ImageError("no two top models")))
    assert main([]) == 1
    assert "images refused: no two top models" in capsys.readouterr().err


def test_committed_baseline_names_the_split_cases_of_the_truth():
    baseline = json.loads((ROOT / "results" / "human_baseline.json").read_text())
    truth = load_truth()
    split = {c["id"] for c in active_cases(load_cases()) if truth[c["id"]]["version_split"]}
    assert set(baseline["version_split_cases"]["cases"]) == split
