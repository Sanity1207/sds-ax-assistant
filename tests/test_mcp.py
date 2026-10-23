import asyncio
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import ROOT
from app.mcp_server import server_command
from app.tools import (TOOL_SPECS, TOOLS_IMPL, find_action, find_vessel, get_issue, get_issue_trace,
                       get_vessel_mappings, list_issues)


async def _call(db_path, name=None, args=None):
    command, cmd_args = server_command()
    params = StdioServerParameters(command=command, args=cmd_args, cwd=str(ROOT),
                                   env={"APP_DB_PATH": str(db_path)})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = [t.name for t in (await session.list_tools()).tools]
            text = None
            if name:
                result = await session.call_tool(name, args or {})
                text = result.content[0].text
            return tools, text


def test_stdio_lists_and_calls_find_vessel(db_path):
    tools, text = asyncio.run(_call(db_path, "find_vessel", {"query": "혼합"}))
    assert sorted(tools) == sorted(TOOL_SPECS)
    assert "V003" in text and "혼합탱크 1" in text


def test_specs_match_impl():
    assert set(TOOL_SPECS) == set(TOOLS_IMPL)


def test_find_vessel_formats(db_path):
    assert find_vessel("혼합") == "VESSEL 검색 '혼합': 1건\n- V003 혼합탱크 1"
    assert find_vessel("v00").startswith("VESSEL 검색 'v00': 5건")
    assert find_vessel("").startswith("VESSEL 전체 5건")
    assert find_vessel("없는설비") == "VESSEL 검색 '없는설비': 0건 (일치하는 VESSEL 없음)"


def test_find_vessel_wildcards_are_literal(db_path):
    assert find_vessel("%").endswith("0건 (일치하는 VESSEL 없음)")
    assert find_vessel("_").endswith("0건 (일치하는 VESSEL 없음)")


# ---- T17: 읽기 도구 6개 ----

import pytest  # noqa: E402

from app.classify import register_issue  # noqa: E402
from app.fake_llm import FakeChatModel, fake_llm  # noqa: E402


def test_stdio_calls_each_read_tool(db_path):
    for name, args, expected in [("find_action", {"query": "냉각"}, "104 냉각"),
                                 ("get_vessel_mappings", {"vessel_id": "V003"}, "V003 → 103 교반"),
                                 ("list_issues", {"status": "반려"}, "#9 · 반려"),
                                 ("get_issue", {"issue_no": 4}, "수행됨 Y"),
                                 ("get_issue_trace", {"issue_no": 2}, "시드로 넣은 건이라 기록 없음")]:
        _, text = asyncio.run(_call(db_path, name, args))
        assert expected in text, (name, text)


def test_stdio_wrong_type_is_error(db_path):
    _, text = asyncio.run(_call(db_path, "get_issue", {"issue_no": "abc"}))
    assert text.startswith("[오류]") and "list_issues" in text


def test_find_action_formats(db_path):
    assert find_action("냉각") == "ACTION 검색 '냉각': 1건\n- 104 냉각"
    assert find_action("10").startswith("ACTION 검색 '10': 6건")
    assert find_action("").startswith("ACTION 전체 6건")
    assert find_action(" ") .startswith("ACTION 전체 6건")
    assert find_action("믹싱") == "ACTION 검색 '믹싱': 0건 (일치하는 ACTION 없음)"  # 0건은 오류가 아님


def test_search_rejects_non_text(db_path):
    for fn in (find_vessel, find_action):
        text = fn(123)
        assert text.startswith("[오류]") and "받은 값: 123" in text and "다음 행동" in text


def test_get_vessel_mappings(db_path):
    assert get_vessel_mappings("V001").splitlines() == [
        "V001 반응기 A의 매핑 3건",
        "- V001 → 101 원료 투입 · 수행됨 Y(2026-10-20 09:00:00)",
        "- V001 → 102 가열 · 수행됨 Y(2026-10-20 10:30:00)",
        "- V001 → 103 교반 · 미수행 N"]
    everything = get_vessel_mappings("")
    assert everything.startswith("전체 매핑 7건") and everything.count("\n- ") == 7
    assert "[V005 세정조 1]" in everything
    assert get_vessel_mappings(" V004 ").startswith("V004 저장탱크 1의 매핑 1건")


def test_get_vessel_mappings_errors(db_path):
    text = get_vessel_mappings("혼합탱크")
    assert text.startswith("[오류] VESSEL_ID '혼합탱크'는 VESSEL_MST에 없습니다.")
    assert "쓸 수 있는 값: V001 반응기 A" in text and "find_vessel" in text


def test_get_vessel_mappings_empty_vessel(db_path):
    from app.db import connect

    conn = connect()
    with conn:
        conn.execute("DELETE FROM VESSEL_ACTION WHERE VESSEL_ID = 'V005'")
    assert get_vessel_mappings("V005") == "V005 세정조 1의 매핑 0건 (연결된 ACTION 없음)"


def test_list_issues(db_path):
    text = list_issues()
    assert text.splitlines()[0] == "이슈 9건(전체)" and text.splitlines()[1].startswith("- #9 · 반려")
    assert list_issues(limit=2).splitlines()[0] == "이슈 9건(전체), 최근 2건 표시"
    assert list_issues("처리대기").splitlines()[0] == "이슈 3건(처리상태 처리대기)"
    assert list_issues(since="2026-10-22 11:00:00").splitlines()[0] == "이슈 2건(접수 2026-10-22 11:00:00 이후)"
    assert list_issues(since="2026-10-22").splitlines()[0] == "이슈 8건(접수 2026-10-22 00:00:00 이후)"
    assert list_issues("거절") == "이슈 0건(처리상태 거절) (조건에 맞는 이슈 없음)"


@pytest.mark.parametrize("kwargs, part", [
    ({"status": "열림"}, "처리상태 '열림'는 없습니다."), ({"since": "어제"}, "since 형식이 맞지 않습니다."),
    ({"since": "2026/10/22"}, "since 형식"), ({"limit": 0}, "limit은 1~50"), ({"limit": 51}, "limit은 1~50"),
])
def test_list_issues_errors(db_path, kwargs, part):
    text = list_issues(**kwargs)
    assert text.startswith(f"[오류] {part}") and "쓸 수 있는 값" in text and "다음 행동" in text


def test_get_issue(db_path):
    text = get_issue(4)
    assert text.splitlines()[0] == "이슈 #4"
    assert "- VESSEL_ID 칸: V004 저장탱크 1" in text and "- ACTION_SEQ 칸: 105 배출" in text
    assert "- 입력칸 매핑 현재 상태: V004 → 105 매핑 있음 · 수행됨 Y(2026-10-21 16:20:00)" in text
    assert "<<<저장탱크 1(V004)에서 배출(105) 연결을 삭제해 주세요." in text and "처리 이력 없음" in text
    assert "- 입력칸 매핑 현재 상태: V003 → 104 매핑 없음" in get_issue(2)
    done = get_issue("#1")
    assert "처리 이력 1건" in done and "실행 성공" in done and "처리자: A001" in done


@pytest.mark.parametrize("fn", [get_issue, get_issue_trace])
@pytest.mark.parametrize("value, part", [(999, "이슈 #999는 없습니다."), (0, "issue_no는 1 이상"), ("abc", "issue_no는 1 이상"),
                                         (None, "issue_no는 1 이상"), (True, "issue_no는 1 이상")])
def test_issue_tools_errors(db_path, fn, value, part):
    text = fn(value)
    assert text.startswith(f"[오류] {part}") and "다음 행동" in text


def test_get_issue_trace_summary(db_path):
    answer = {"type": "매핑 생성", "multiple_requests": False, "id_mismatch": False, "reason": "V003에 104 추가 요청"}
    out = register_issue("C001", "V003", "104", "V003에 104 추가해 주세요", fake_llm(FakeChatModel("fake-1", [answer])))
    text = get_issue_trace(out.issue_no)
    lines = text.splitlines()
    assert lines[0] == f"이슈 #{out.issue_no} 처리 기록: 실행 1회"
    assert "· 문의 등록 · 사용자 SYSTEM · thread - · 결과 분류 완료 · 도구 0회" in lines[1]
    assert any("[branch] 등록 전 검사: 통과" in line for line in lines)
    assert any("[llm] classify@v2: 성공 · 모델 fake-1" in line for line in lines)
    assert any("[branch] 규칙표:" in line and "처리대기" in line for line in lines)
    assert get_issue_trace(2) == "이슈 #2 처리 기록: 시드로 넣은 건이라 기록 없음"
