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
