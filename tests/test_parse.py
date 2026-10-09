from dataclasses import dataclass

import pytest

from memtrap.parse import confidence, letter, number, parse_response


def test_plain_json():
    r = parse_response('{"answer": "C", "estimate": 40, "mechanism": "B", "confidence": 80, "reasoning": "hash"}')
    assert r == {"answer": "C", "estimate": 40.0, "mechanism": "B", "confidence": 80, "reasoning": "hash"}


def test_json_in_code_fence_with_prose():
    text = 'Sure.\n```json\n{"answer": "b", "estimate": "16 MB", "mechanism": "Option D", "confidence": "90%"}\n```\nDone.'
    r = parse_response(text)
    assert r["answer"] == "B" and r["mechanism"] == "D" and r["estimate"] == 16.0 and r["confidence"] == 90


def test_braces_inside_strings_do_not_break_json():
    text = '{"answer": "A", "estimate": 2, "mechanism": "A", "confidence": 70, "reasoning": "go func() { <-done }()"}'
    assert parse_response(text, unit="KB")["reasoning"] == "go func() { <-done }()"


def test_line_fallback_when_json_is_broken():
    text = "answer: D\nestimate: 40\nmechanism: C\nconfidence: 65\nreasoning: depends on version"
    r = parse_response(text)
    assert r["answer"] == "D" and r["estimate"] == 40.0 and r["mechanism"] == "C" and r["confidence"] == 65


def test_truncated_json_falls_back_to_lines():
    text = '{"answer": "B", "estimate": 36, "mechanism": "B", "confid'
    r = parse_response(text)
    assert r["answer"] == "B" and r["estimate"] == 36.0 and r["confidence"] is None


def test_garbage_is_a_parse_failure():
    assert parse_response("I cannot answer that.") is None
    assert parse_response("") is None
    assert parse_response(None) is None
    assert parse_response('{"foo": 1}') is None


def test_structured_output_object():
    @dataclass
    class Reply:
        answer: str
        estimate: float
        mechanism: str
        confidence: int
        reasoning: str

    r = parse_response(Reply("A", 2.0, "A", 99, "2 KB"), unit="KB")
    assert r["answer"] == "A" and r["confidence"] == 99


def test_dict_reply():
    assert parse_response({"answer": "C", "estimate": 1, "mechanism": "x"})["mechanism"] is None


@pytest.mark.parametrize("value,expected", [
    ("A", "A"), (" c ", "C"), ("B.", "B"), ("(D)", "D"), ("Option A", "A"),
    ("B. About the same", "B"), ("E", None), ("AB", None), (3, None), (None, None), ("", None),
])
def test_letter(value, expected):
    assert letter(value) == expected


@pytest.mark.parametrize("value,unit,expected", [
    (40, "MB", 40.0), ("40", "MB", 40.0), ("~40 MB", "MB", 40.0), ("41943040 bytes", "MB", 40.0),
    ("2 KB", "KB", 2.0), ("2048 B", "KB", 2.0), ("0.5 GB", "MB", 512.0), ("16 KiB", "KB", 16.0),
    ("1,048,576 bytes", "MB", 1.0), ("16 MB", "KB", 16384.0), (True, "MB", None), ("lots", "MB", None),
    (float("nan"), "MB", None),
])
def test_number_units(value, unit, expected):
    assert number(value, unit) == expected


@pytest.mark.parametrize("value,expected", [
    (80, 80), ("85%", 85), (0, 0), (100, 100), (101, None), (-1, None), ("high", None), (99.6, 100), (True, None),
])
def test_confidence_range(value, expected):
    assert confidence(value) == expected
