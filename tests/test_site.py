import json
import re

from memtrap.catalog import load_cases, load_truth
from memtrap.site import case_data, render_page


def test_page_embeds_every_case_with_measured_values():
    cases, truth = load_cases(), load_truth()
    data = case_data(cases, truth)
    assert len(data) == 17
    p1 = next(d for d in data if d["id"] == "P1")
    assert p1["split"] is True and p1["cold"] == truth["P1"]["cold_answer"]
    assert [m["estimate"] for m in p1["measured"]] == [truth["P1"]["versions"][v]["estimate"] for v in cases[0]["versions"]]


def test_page_is_self_contained():
    cases, truth = load_cases(), load_truth()
    page = render_page(case_data(cases, truth))
    assert not re.search(r'<(script|link|img)[^>]+(src|href)="https?://', page)
    assert "fonts.googleapis" not in page
    blob = page.split("const CASES = ", 1)[1].split(";\nconst KEY", 1)[0]
    assert len(json.loads(blob)) == 17
