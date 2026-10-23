"""사용자와 권한. 비밀번호 없이 사용자를 고르고, 권한은 USER_MST.ROLE로 정한다.

권한 검사는 두 겹이다: 화면은 권한에 맞는 메뉴·버튼만 보여 주고, 등록·조사·승인 함수는 안에서 이 모듈로 다시 확인한다.
SYSTEM(권한 '시스템')은 자동 분류가 행동한 사용자로만 쓰며, 사람이 고를 수 없고 어떤 행동 권한도 없다.
"""

import sqlite3

from app.db import connect

# 행동: read = 이슈대장 읽기, register = 문의 등록, investigate = 조사 비서 대화, approve = 처리안 승인·[다시 분류]
PERMISSIONS: dict[str, frozenset[str]] = {
    "조회 고객": frozenset({"read"}),
    "요청 고객": frozenset({"read", "register"}),
    "관리자": frozenset({"read", "register", "investigate", "approve"}),
}
ACTION_NAMES = {"read": "이슈대장 조회", "register": "문의 등록", "investigate": "조사 비서 사용", "approve": "처리안 승인"}
DEFAULT_USER = "A001"


class PermissionDenied(Exception):
    """사용자가 없거나 그 행동을 할 권한이 없다."""


def get_user(user_id: str, conn: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    own = conn is None
    conn = conn or connect()
    try:
        return conn.execute("SELECT * FROM USER_MST WHERE USER_ID = ?", (user_id,)).fetchone()
    finally:
        if own:
            conn.close()


def selectable_users(conn: sqlite3.Connection | None = None) -> list[sqlite3.Row]:
    """사용자 선택 드롭다운에 나오는 사용자(SYSTEM 제외)."""
    own = conn is None
    conn = conn or connect()
    try:
        return conn.execute("SELECT * FROM USER_MST WHERE ROLE <> '시스템' ORDER BY USER_ID").fetchall()
    finally:
        if own:
            conn.close()


def can(role: str | None, action: str) -> bool:
    return action in PERMISSIONS.get(role or "", frozenset())


def require(user_id: str, action: str, conn: sqlite3.Connection | None = None) -> sqlite3.Row:
    """권한이 있으면 사용자 행을 돌려주고, 없으면 PermissionDenied(사유 문장)."""
    user = get_user(user_id, conn)
    if user is None:
        raise PermissionDenied(f"사용자 '{user_id}'가 USER_MST에 없습니다.")
    if not can(user["ROLE"], action):
        allowed = ", ".join(r for r, acts in PERMISSIONS.items() if action in acts)
        raise PermissionDenied(f"{user['USER_NAME']}({user_id}, {user['ROLE']})에게는 {ACTION_NAMES[action]} 권한이 없습니다."
                               f" 가능한 권한: {allowed}")
    return user
