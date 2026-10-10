import copy

import pytest
from PIL import Image

from memtrap.images import ImageError, collect, control_pair, write_all


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
    assert (data["cases"], data["runtime_versions"]) == (17, 8) and data["models"] == len(board)


def test_control_pair_comes_from_the_runs_and_the_truth(data):
    control = data["control"]
    assert control["id"] == "R4" and control["metric"] == "StackInuse"
    assert [m["model"] for m in control["said"]] == [row["model"] for row in data["board"][:2]]
    assert [m["version"] for m in control["measured"]] == ["Go 1.23", "Go 1.24", "Go 1.25", "Go 1.26"]
    pair = control_pair(data)
    assert pair == {"said": 16.0, "measured": 2.0, "unit": "KB"}
    for model in control["said"]:
        assert len(model["confidences"]) == 3 and not any(model["right"])


def test_card_is_refused_when_a_top_model_gets_the_control_right(data):
    changed = copy.deepcopy(data)
    changed["control"]["said"][0]["right"][0] = True
    with pytest.raises(ImageError):
        control_pair(changed)
    changed = copy.deepcopy(data)
    changed["control"]["measured"][0]["value"] = 16.0
    with pytest.raises(ImageError):
        control_pair(changed)


def test_baseline_line_uses_the_reported_counts(data):
    b = data["baseline"]
    assert b is not None
    assert b["computed_score"] == (b["answers_right"] + b["mechanisms_right"] + b["estimates_in_tolerance"]) / (3 * b["cases"])


def test_images_have_the_sizes_the_post_needs(tmp_path):
    cover, board, card = write_all(tmp_path)
    with Image.open(cover) as im:
        assert im.size == (1000, 420) and im.mode == "RGB"
    with Image.open(board) as im:
        assert im.size[0] == 1600
    with Image.open(card) as im:
        assert im.size == (1200, 675)
