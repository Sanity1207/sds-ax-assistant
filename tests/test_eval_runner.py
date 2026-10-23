"""평가셋·평가 실행기(묶음 8). 가짜 LLM만 쓴다."""

import json

import pytest

from app.evaluate import CATEGORIES, CaseError, load_cases, main, validate_case


# ---- T33: 평가셋 ----

def test_cases_load_15_with_distribution():
    cases = load_cases()
    assert len(cases) == 15
    assert {k: sum(1 for c in cases if c["category"] == k) for k in CATEGORIES} == {
        "classify": 4, "free": 3, "approval": 4, "anomaly": 2, "reapply": 2}


def test_cases_check_command(capsys):
    assert main(["--check"]) == 0
    assert "평가셋 15건" in capsys.readouterr().out


def test_cases_validation_catches_missing(tmp_path):
    case = dict(load_cases()[0])
    del case["expected"]
    assert validate_case(case) == ["C1: 필수 칸 'expected' 없음"]
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"case_id": "X", "category": "approval", "description": "", "input": {"turns": ["y"]},
                               "expected": {}, "required_facts": [], "required_tools": [], "fake": [{"oops": 1}]},
                              ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CaseError) as info:
        load_cases(bad)
    text = str(info.value)
    assert "expected.proposal" in text and "input.approvals" in text and "fake 응답 형식 오류" in text
    assert "분포가 다릅니다" in text
