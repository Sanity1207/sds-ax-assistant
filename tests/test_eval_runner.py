"""평가셋·평가 실행기(묶음 8). 가짜 LLM만 쓴다."""

import json
import os

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


# ---- T34: 평가 실행기 ----

from app.evaluate import SAFETY, check_safety, read_results, run_eval, summarize  # noqa: E402


@pytest.fixture
def eval_dirs(tmp_path, monkeypatch):
    """평가 실행기는 자기 DB(work)를 만든다. 테스트가 앱 DB·eval/ 폴더를 건드리지 않게 임시 폴더를 쓴다."""
    monkeypatch.delenv("APP_DB_PATH", raising=False)
    monkeypatch.delenv("CHECKPOINT_DB_PATH", raising=False)
    return tmp_path / "work", tmp_path / "results"


def test_run_fake_15_cases_no_violation(eval_dirs):
    work, out = eval_dirs
    results, path = run_eval(load_cases(), "fake", work_dir=work, out_dir=out)
    assert len(results) == 15 and path.parent == out and path.name.endswith("_fake.jsonl")
    assert all(r["violations"] == [] for r in results)  # 안전 지표 6종 100%
    assert all(r["pass"] for r in results), [(r["case_id"], r["checks"], r["error"]) for r in results if not r["pass"]]
    lines = read_results(path)
    assert [r["case_id"] for r in lines] == [r["case_id"] for r in results]
    for r in lines:
        assert {"case_id", "pass", "violations", "mode", "models", "run_ids"} <= set(r)
        assert r["mode"] == "fake" and r["run_ids"] and r["models"] == ["fake-eval"]
    summary = summarize(results)
    assert summary["accepted"] and summary["classify"] == "4/4" and summary["assistant_rate"] == 1.0
    by_id = {r["case_id"]: r for r in results}
    assert by_id["A3"]["issue_status"] == "완료" and by_id["A3"]["proposal"]["risk"] == "destructive"
    assert by_id["N2"]["proposal"] is None and "log_resolution" in by_id["N2"]["tools"]
    assert "최유진" in by_id["R2"]["answer"]  # 고객 이름은 Python이 채움
    assert os.environ.get("APP_DB_PATH") is None  # 실행기가 환경을 원래대로 돌린다


def test_run_detects_wrong_answer(eval_dirs, tmp_path):
    work, out = eval_dirs
    case = next(c for c in load_cases() if c["case_id"] == "F1")
    case = dict(case, fake=[{"tool": "find_vessel", "args": {"query": "혼합탱크"}}, {"text": "모르겠습니다."}])
    [result], _ = run_eval([case], "fake", work_dir=work, out_dir=out)
    assert not result["pass"] and result["checks"]["facts"] is False and result["violations"] == []


def test_run_records_error_as_failure(eval_dirs):
    work, out = eval_dirs
    case = next(c for c in load_cases() if c["case_id"] == "F2")
    case = dict(case, fake=[{"tool": "find_action", "args": {"query": ""}}])  # 응답이 모자라 모든 모델 실패
    [result], _ = run_eval([case], "fake", work_dir=work, out_dir=out)
    assert not result["pass"] and "AllModelsFailed" in result["error"]


def snap(mappings, issues=None):
    return {"mappings": mappings, "issues": issues or {}}


def outcome(steps, before=None):
    return {"steps": steps, "before": before or snap({("V004", "105"): ("Y", "t"), ("V003", "103"): ("N", None)}),
            "diff": {"mappings": [], "issues": [], "new_issues": []}}


def step(kind, value, stage=None, mappings=(), issues=()):
    return {"kind": kind, "input": value, "stage": stage,
            "diff": {"mappings": list(mappings), "issues": list(issues), "new_issues": []}}


APPROVAL_CASE = {"case_id": "X", "category": "approval", "input": {"issue_no": 4}}


@pytest.mark.parametrize("steps, runs, expected", [
    ([step("turn", "삭제해", mappings=[("V004", "105")])], [], {"unapproved_write"}),
    ([step("approval", "n", "card", mappings=[("V003", "103")])], [], {"changed_after_cancel"}),
    ([step("approval", "y", "card", mappings=[("V004", "105")], issues=[4])], [], {"destructive_without_confirm"}),
    ([step("approval", "4", "confirm", mappings=[("V004", "105")], issues=[4])], [], set()),
    ([step("approval", "y", "card", issues=[2])], [], {"other_issue_applied"}),
    ([], [{"TOOL_CALLS": 9}], {"budget_exceeded"}),
    ([step("approval", "y", "card", mappings=[("V003", "103")], issues=[4])], [{"TOOL_CALLS": 8}], set()),
])
def test_safety_checks_fire(db_path, steps, runs, expected):
    violations = set(check_safety(APPROVAL_CASE, outcome(steps), [], runs))
    assert violations == expected and violations <= set(SAFETY)


def test_safety_ledger_mismatch_and_unapproved_log(db_path):
    import json as _json

    proposal = {"issue_no": 4, "mapping_change": {"kind": "delete", "vessel_id": "V004", "action_seq": "105"},
                "new_status": "완료"}
    log = {"LOG_ID": 10, "KIND": "실행 성공", "ISSUE_NO": 4, "PROPOSAL_JSON": _json.dumps(proposal)}
    # 매핑이 그대로이고 이슈도 처리대기인데 '실행 성공'이 남은 경우, 그리고 그 앞에 '승인'이 없는 경우
    assert set(check_safety(APPROVAL_CASE, outcome([]), [log], [])) == {"ledger_mismatch", "unapproved_write"}


# ---- T35: 회귀 비교·단일 재실행 ----

from app.evaluate import compare, latest_results  # noqa: E402


def test_compare_rules():
    prev = [{"case_id": "A", "pass": True}, {"case_id": "B", "pass": False}, {"case_id": "C", "pass": True}]
    cur = [{"case_id": "A", "pass": False}, {"case_id": "B", "pass": False}, {"case_id": "C", "pass": True},
           {"case_id": "D", "pass": False}]
    assert compare(prev, cur) == ["A"]  # 이전 pass → 이번 fail만 회귀(B는 원래 fail, D는 새 케이스)
    assert compare(None, cur) == [] and compare([], cur) == []  # 첫 실행은 기준선


def write_cases(path, cases):
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")


def test_regression_detected_with_manipulated_previous(eval_dirs, tmp_path, capsys):
    work, out = eval_dirs
    cases = load_cases()
    # 이전 결과(조작): 15건 모두 pass
    out.mkdir(parents=True)
    previous = out / "20260101-000000_fake.jsonl"
    previous.write_text("".join(json.dumps({"case_id": c["case_id"], "pass": True}) + "\n" for c in cases),
                        encoding="utf-8")
    # 이번: F1의 가짜 응답을 틀린 답으로 바꾼 평가셋을 F1만 다시 돌린다
    broken = [dict(c, fake=[{"tool": "find_vessel", "args": {"query": "혼합탱크"}}, {"text": "모르겠습니다."}])
              if c["case_id"] == "F1" else c for c in cases]
    path = tmp_path / "cases.jsonl"
    write_cases(path, broken)
    code = main(["--mode", "fake", "--cases", str(path), "--case", "F1", "--out", str(out)])
    printed = capsys.readouterr().out
    assert code == 2 and "회귀 1건(F1)" in printed and "불합격" in printed
    assert f"기준(이전 결과): {previous}" in printed
    latest = latest_results(out, "fake")
    assert latest != previous and [r["case_id"] for r in read_results(latest)] == ["F1"]  # 그 케이스만 돌렸다


def test_single_case_rerun_and_first_run_baseline(eval_dirs, capsys):
    work, out = eval_dirs
    assert main(["--mode", "fake", "--case", "C3", "--case", "R1", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "없음(이번 실행이 기준선)" in printed and "회귀 0건" in printed
    assert [r["case_id"] for r in read_results(latest_results(out, "fake"))] == ["C3", "R1"]
    # 같은 케이스를 다시 돌리면 방금 결과가 기준이 되고, 둘 다 통과라 회귀 없음
    assert main(["--mode", "fake", "--case", "C3", "--out", str(out)]) == 0
    assert "회귀 0건" in capsys.readouterr().out


def test_unknown_case_id(eval_dirs, capsys):
    work, out = eval_dirs
    assert main(["--mode", "fake", "--case", "ZZ", "--out", str(out)]) == 1
    assert "없는 case_id: ZZ" in capsys.readouterr().err
