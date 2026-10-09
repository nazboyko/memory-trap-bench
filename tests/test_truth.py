import json

import pytest

from memtrap.catalog import by_id
from memtrap.truth import build_truth, group_readings, load_reports, version_key
from tests.conftest import letter_of

MB = 1 << 20


def php_report(version, p1a, p1b):
    return {
        "runtime": "php", "version": version, "sapi": "cli", "os": "Linux",
        "thread_safe": False, "opcache": False,
        "readings": [
            {"case": "P1", "variant": "a", "metric": "memory_get_usage", "unit": "bytes", "value": p1a, "n": 1_000_000},
            {"case": "P1", "variant": "b", "metric": "memory_get_usage", "unit": "bytes", "value": p1b, "n": 1_000_000},
        ],
    }


def write_reports(tmp_path, named):
    for name, report in named.items():
        (tmp_path / name).write_text(json.dumps(report))
    return load_reports(tmp_path)


def p1_only(cases):
    return [by_id(cases)["P1"]]


def test_version_key():
    assert version_key({"runtime": "php", "version": "8.3.12"}) == "php-8.3"
    assert version_key({"runtime": "go", "version": "go1.24.9"}) == "go-1.24"


def test_median_and_split(tmp_path, cases):
    reports = write_reports(tmp_path, {
        "gha-php-8.1-1.json": php_report("8.1.33", 32 * MB, 40 * MB),
        "gha-php-8.1-2.json": php_report("8.1.33", 32 * MB, 40 * MB + 10),
        "gha-php-8.1-3.json": php_report("8.1.33", 32 * MB, 99 * MB),
        "gha-php-8.3-1.json": php_report("8.3.20", 16 * MB, 40 * MB),
    })
    truth = build_truth(p1_only(cases), group_readings(reports))
    t = truth["P1"]
    assert t["versions"]["php-8.1"]["values"]["b"] == 40 * MB + 10
    assert t["versions"]["php-8.1"]["readings"] == 3
    p1 = by_id(cases)["P1"]
    assert t["versions"]["php-8.1"]["answer"] == letter_of(p1, "$b takes about 25%")
    assert t["versions"]["php-8.3"]["answer"] == letter_of(p1, "$b takes about 2.5x")
    assert t["version_split"] is True and t["cold_answer"] == p1["version_option"]
    assert t["versions"]["php-8.3"]["estimate"] == 40.0


def test_actions_reading_wins_over_local(tmp_path, cases):
    reports = write_reports(tmp_path, {
        "local-php-8.3-1.json": php_report("8.3.1", 40 * MB, 40 * MB),
        "gha-php-8.3-1.json": php_report("8.3.20", 16 * MB, 40 * MB),
        "gha-php-8.1-1.json": php_report("8.1.33", 16 * MB, 40 * MB),
    })
    t = build_truth(p1_only(cases), group_readings(reports))["P1"]
    assert t["versions"]["php-8.3"]["source"] == "gha"
    assert t["version_split"] is False and t["cold_answer"] == letter_of(by_id(cases)["P1"], "$b takes about 2.5x")


def test_missing_version_leaves_case_incomplete(tmp_path, cases):
    reports = write_reports(tmp_path, {"gha-php-8.3-1.json": php_report("8.3.20", 16 * MB, 40 * MB)})
    t = build_truth(p1_only(cases), group_readings(reports))["P1"]
    assert t["complete"] is False and t["cold_answer"] is None


def test_measurement_between_options_is_unresolved(tmp_path, cases):
    reports = write_reports(tmp_path, {
        "gha-php-8.1-1.json": php_report("8.1.33", 10 * MB, 18 * MB),
        "gha-php-8.3-1.json": php_report("8.3.20", 16 * MB, 40 * MB),
    })
    t = build_truth(p1_only(cases), group_readings(reports))["P1"]
    assert t["versions"]["php-8.1"]["answer"] is None
    assert t["complete"] is False and t["cold_answer"] is None


def test_missing_variant_stops(tmp_path, cases):
    report = php_report("8.3.20", 16 * MB, 40 * MB)
    report["readings"] = report["readings"][:1]
    reports = write_reports(tmp_path, {"gha-php-8.3-1.json": report})
    with pytest.raises(SystemExit):
        build_truth(p1_only(cases), group_readings(reports))
