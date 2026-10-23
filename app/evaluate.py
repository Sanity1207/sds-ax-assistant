"""평가셋·평가 실행기.

    python -m app.evaluate --check                      평가셋 검사(건수·분포·필수 필드)

평가셋(eval/cases.jsonl)은 LLM 판단이 결과를 좌우하는 15건만 다룬다. 승인·취소·직접 편집, 권한, 미들웨어 차단처럼
Python이 정하는 것은 pytest가 다룬다. 케이스 한 줄의 칸:
- case_id, category(classify·free·approval·anomaly·reapply), description
- input: classify = 요청자·VESSEL_ID·ACTION_SEQ·문의내용 / 그 밖 = 관리자, 이슈 번호(없으면 이슈 없는 대화),
  대화 입력(turns, 열린 이슈의 첫 입력은 첫 질문 답 y), 미리 정한 승인 응답(approvals: y/n/e·이슈 번호·{"edited": 편집 값})
- expected: classify = 처리상태·담당자 / ①·③ = 처리안 내용(변경 종류·대상·새 상태) / 끝난 뒤 이슈 처리상태 등
- required_facts: 최종 답에 들어 있어야 하는 핵심 사실(문자열 포함 비교) / required_tools: 반드시 부른 도구(순서 무관)
- fake: 가짜 LLM 모드에서 쓸 응답 순서({"structured": …} / {"tool": 이름, "args": …} / {"text": …})
- setup(선택): 케이스 전에 넣을 이슈(시드 상태 위에 추가)
"""

import json
from pathlib import Path

from app.config import ROOT

CASES_PATH = ROOT / "eval" / "cases.jsonl"
CATEGORIES = {"classify": 4, "free": 3, "approval": 4, "anomaly": 2, "reapply": 2}
CATEGORY_NAMES = {"classify": "자동 분류", "free": "② 자유 질의", "approval": "① 처리안", "anomaly": "③ 값 이상",
                  "reapply": "④ 반려 건"}
REQUIRED = ("case_id", "category", "description", "input", "expected", "required_facts", "required_tools", "fake")


class CaseError(ValueError):
    """평가셋 형식이 맞지 않는다."""


def validate_case(case: dict) -> list[str]:
    """케이스 한 건의 형식 검사. 문제 문장 목록(없으면 빈 목록)."""
    problems = [f"필수 칸 '{k}' 없음" for k in REQUIRED if k not in case]
    if problems:
        return [f"{case.get('case_id', '?')}: {p}" for p in problems]
    cid, category, given, expected = case["case_id"], case["category"], case["input"], case["expected"]
    if category not in CATEGORIES:
        problems.append(f"category '{category}'는 없습니다(쓸 수 있는 값: {', '.join(CATEGORIES)})")
    if not case["fake"]:
        problems.append("fake 응답이 비어 있습니다")
    if category == "classify":
        for k in ("requester_id", "vessel_id", "action_seq", "inquiry"):
            if k not in given:
                problems.append(f"input.{k} 없음")
        for k in ("status", "owner_role"):
            if k not in expected:
                problems.append(f"expected.{k} 없음(기대 처리상태·담당자)")
    else:
        if not given.get("turns"):
            problems.append("input.turns(대화 입력)가 비어 있습니다")
        if "user_id" not in given or "issue_no" not in given:
            problems.append("input.user_id·input.issue_no 없음")
        if category in ("approval",) and "proposal" not in expected:
            problems.append("expected.proposal(처리안 채점 대상) 없음")
        if category in ("approval",) and not given.get("approvals"):
            problems.append("input.approvals(미리 정한 승인 응답) 없음")
        if not (case["required_facts"] or case["required_tools"] or expected):
            problems.append("채점 기준(required_facts·required_tools·expected)이 하나도 없습니다")
    for item in case["fake"]:
        if not isinstance(item, dict) or not ({"structured", "tool", "text"} & set(item)):
            problems.append(f"fake 응답 형식 오류: {item!r}")
    return [f"{cid}: {p}" for p in problems]


def load_cases(path: Path | str = CASES_PATH, *, check_distribution: bool = True) -> list[dict]:
    """평가셋을 읽고 검사한다. 문제가 있으면 CaseError(문제 전체)."""
    cases, problems = [], []
    for no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            cases.append(json.loads(line))
        except ValueError as e:
            problems.append(f"{no}번째 줄 JSON 오류: {e}")
    for case in cases:
        problems += validate_case(case)
    ids = [c.get("case_id") for c in cases]
    if len(ids) != len(set(ids)):
        problems.append("case_id가 겹칩니다")
    if check_distribution:
        counts = {k: sum(1 for c in cases if c.get("category") == k) for k in CATEGORIES}
        if counts != CATEGORIES or len(cases) != sum(CATEGORIES.values()):
            problems.append(f"분포가 다릅니다: {counts} (기대 {CATEGORIES}, 합계 {sum(CATEGORIES.values())}건)")
    if problems:
        raise CaseError("\n".join(problems))
    return cases


def distribution(cases: list[dict]) -> str:
    return " / ".join(f"{CATEGORY_NAMES[k]} {sum(1 for c in cases if c['category'] == k)}" for k in CATEGORIES)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="python -m app.evaluate", description="평가셋 검사·평가 실행")
    parser.add_argument("--check", action="store_true", help="평가셋 검사만 한다")
    parser.add_argument("--cases", default=str(CASES_PATH), help="평가셋 파일")
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.cases)
    except CaseError as e:
        print(f"평가셋 오류:\n{e}", file=sys.stderr)
        return 1
    print(f"평가셋 {len(cases)}건 · {distribution(cases)} · 필수 칸 누락 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
