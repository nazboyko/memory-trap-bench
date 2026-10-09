import copy
import json

import pytest

from memtrap.catalog import load_cases


@pytest.fixture
def cases():
    return copy.deepcopy(load_cases())


@pytest.fixture
def write_catalog(tmp_path):
    def write(cases, truth=None):
        cpath = tmp_path / "cases.json"
        tpath = tmp_path / "truth.json"
        cpath.write_text(json.dumps(cases))
        if truth is not None:
            tpath.write_text(json.dumps(truth))
        return cpath, tpath

    return write


def letter_of(case, text_start):
    """The option letter whose text starts with text_start."""
    hits = [k for k, v in case["options"].items() if v.startswith(text_start)]
    assert len(hits) == 1, (case["id"], text_start, hits)
    return hits[0]
