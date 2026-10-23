"""MCP 도구: 메뉴판(TOOL_SPECS)과 구현(TOOLS_IMPL)을 나눈다.

TOOL_SPECS는 LLM에 보이는 설명과 Python이 쓰는 위험 등급을, TOOLS_IMPL은 실제 함수를 담는다.
두 목록의 이름이 다르면 import할 때 AssertionError로 멈춘다.
도구는 정해진 형식의 사람이 읽는 문자열을 돌려주고, 오류는 예외 대신 '[오류]'로 시작하는 안내 문자열로 돌려준다(T17).
묶음 1(T05)은 find_vessel 하나만 있다.
"""

from typing import Annotated

from pydantic import Field

from app.db import connect

TOOL_SPECS: dict[str, dict] = {
    "find_vessel": {
        "description": (
            "VESSEL을 ID나 이름 일부로 검색해 후보 목록(VESSEL_ID, 이름)을 돌려준다. "
            "검색어를 비우면 전체 목록과 개수를 돌려준다. 예: '혼합' → V003 혼합탱크 1"
        ),
        "risk": "read",
    },
}


def find_vessel(query: Annotated[str, Field(description="VESSEL_ID나 이름의 일부. 비우면 전체")] = "") -> str:
    q = (query or "").strip()
    # 검색어의 %·_는 글자 그대로 찾는다(LIKE 와일드카드로 쓰지 않음).
    like = "%" + q.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT VESSEL_ID, VESSEL_NAME FROM VESSEL_MST"
            " WHERE UPPER(VESSEL_ID) LIKE UPPER(?) ESCAPE '!' OR VESSEL_NAME LIKE ? ESCAPE '!'"
            " ORDER BY VESSEL_ID", (like, like)).fetchall()
    finally:
        conn.close()
    head = f"VESSEL 전체 {len(rows)}건" if not q else f"VESSEL 검색 '{q}': {len(rows)}건"
    if not rows:
        return head + " (일치하는 VESSEL 없음)"
    return "\n".join([head] + [f"- {r['VESSEL_ID']} {r['VESSEL_NAME']}" for r in rows])


TOOLS_IMPL = {
    "find_vessel": find_vessel,
}

assert set(TOOL_SPECS) == set(TOOLS_IMPL), f"TOOL_SPECS와 TOOLS_IMPL 이름 불일치: {set(TOOL_SPECS) ^ set(TOOLS_IMPL)}"
