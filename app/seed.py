"""시드 스크립트: data/app.db를 처음 상태로 만든다. 다시 실행하면 초기화한다.

실행: python -m app.seed
"""

from pathlib import Path

from app.config import checkpoint_db_path, db_path
from app.db import SCHEMA, connect

VESSELS = [
    ("V001", "반응기 A"),
    ("V002", "반응기 B"),
    ("V003", "혼합탱크 1"),
    ("V004", "저장탱크 1"),
    ("V005", "세정조 1"),
]

ACTIONS = [
    ("101", "원료 투입"),
    ("102", "가열"),
    ("103", "교반"),
    ("104", "냉각"),
    ("105", "배출"),
    ("106", "세정"),
]

MAPPINGS = [
    ("V001", "101", "Y", "2026-10-20 09:00:00"),
    ("V001", "102", "Y", "2026-10-20 10:30:00"),
    ("V001", "103", "N", None),
    ("V002", "103", "Y", "2026-10-21 14:00:00"),
    ("V003", "103", "N", None),
    ("V004", "105", "Y", "2026-10-21 16:20:00"),
    ("V005", "106", "N", None),
]

USERS = [
    ("C001", "김민수", "요청 고객"),
    ("C002", "이서연", "요청 고객"),
    ("C003", "박지훈", "요청 고객"),
    ("C004", "최유진", "요청 고객"),
    ("C005", "정하늘", "조회 고객"),
    ("A001", "윤도현", "관리자"),
    ("A002", "한지민", "관리자"),
    ("SYSTEM", "시스템", "시스템"),
]

OWNERS = [
    ("매핑 담당자", "윤도현", "내선 1001"),
    ("VESSEL 담당자", "강수진", "내선 1002"),
    ("ACTION 담당자", "오세훈", "내선 1003"),
]

# 시드 이슈 9건. 자동 분류를 거치지 않았으므로 처리 기록(PROCESS_LOG)이 없다. 요청자는 모두 요청 고객(C001~C004).
# 번호는 접수 순서대로 1~9이고, 앱에서 새로 등록하면 10부터 붙는다.
# (접수일시, 요청자, VESSEL_ID, ACTION_SEQ, 문의내용, 담당자, 처리상태, 처리결과, 처리일시, 처리자)
ISSUES = [
    # 1 완료: 매핑 생성 요청을 관리자가 승인해 반영한 건. V002→103 매핑이 실제로 있고, 그 뒤에 수행됐다(Y).
    ("2026-10-19 10:00:00", "C001", "V002", "103", "반응기 B(V002)에 교반(103) 공정을 추가해 주세요.",
     "매핑 담당자", "완료", "[매핑 변경] V002→103 생성. 반응기 B 교반 공정 추가 요청에 따라 연결함",
     "2026-10-21 13:50:00", "A001"),
    # 2 처리대기: 매핑 생성
    ("2026-10-22 09:10:00", "C001", "V003", "104", "혼합탱크 1(V003)에 냉각(104) 공정을 추가해 주세요.",
     "매핑 담당자", "처리대기", "매핑 생성 요청으로 분류. 근거: V003에 104를 추가해 달라는 요청", None, None),
    # 3 처리대기: 매핑 수정(입력칸 ACTION_SEQ는 바꾸기 전 값, 바꿀 값은 문의내용에). V003→103은 미수행(N).
    ("2026-10-22 09:20:00", "C002", "V003", "103", "혼합탱크 1(V003)에 연결된 교반(103)을 배출(105)로 바꿔 주세요.",
     "매핑 담당자", "처리대기", "매핑 수정 요청으로 분류. 근거: V003의 103을 105로 바꿔 달라는 요청", None, None),
    # 4 처리대기: 수행된(Y) 매핑 삭제 = destructive
    ("2026-10-22 09:30:00", "C003", "V004", "105",
     "저장탱크 1(V004)에서 배출(105) 연결을 삭제해 주세요. 이 탱크에서는 더 이상 배출 공정을 하지 않습니다.",
     "매핑 담당자", "처리대기", "매핑 삭제 요청으로 분류. 근거: V004에서 105 연결을 빼 달라는 요청", None, None),
    # 5 확인필요: 값 이상(조사하면 V001→103이 104로 바뀌어야 하는 매핑 오류인지 판단할 건)
    ("2026-10-22 10:00:00", "C004", "V001", "103",
     "반응기 A(V001)에 교반(103)이 연결되어 있는데 이상합니다. 반응기 A는 원료 투입·가열 다음에 냉각을 하는 설비라서 "
     "교반이 아니라 냉각(104)이 맞는 것 같습니다.",
     "매핑 담당자", "확인필요", "값 이상 요청으로 분류. 근거: V001의 103 연결이 맞지 않는 것 같다는 문의", None, None),
    # 6 확인필요: 자동 분류 실패([다시 분류] 대상)
    ("2026-10-22 10:10:00", "C001", "V005", "106", "세정조 1(V005) 세정(106) 건 확인 부탁드립니다.",
     "매핑 담당자", "확인필요", "자동 분류 실패", None, None),
    # 7 이관: ACTION 이름 변경 요청(마스터 요청, 대상 ACTION)
    ("2026-10-22 10:30:00", "C002", "V002", "103", "교반(103)이라는 ACTION 이름을 현장에서 쓰는 '믹싱'으로 바꿔 주세요.",
     "ACTION 담당자", "이관", "마스터 요청(ACTION)으로 분류해 ACTION 담당자에게 이관. 근거: ACTION 103의 이름 변경 요청",
     None, None),
    # 8 반려: 입력칸(V001)과 문의내용이 가리키는 VESSEL(V002)이 다름
    ("2026-10-22 11:00:00", "C003", "V001", "102", "V002 반응기 B에 가열(102)을 추가해 주세요.",
     "매핑 담당자", "반려", "입력칸과 문의내용의 ID가 달라 반려. 근거: VESSEL_ID 칸은 V001인데 문의내용은 V002를 가리킴",
     "2026-10-22 11:00:01", "SYSTEM"),
    # 9 반려: 한 문의에 요청 두 개
    ("2026-10-22 11:30:00", "C004", "V005", "101",
     "세정조 1(V005)에 원료 투입(101)을 추가해 주시고, 세정(106) 연결은 빼 주세요.",
     "매핑 담당자", "반려", "한 문의에 요청이 여러 개라 반려. 근거: V005에 101 추가와 106 삭제 두 가지 요청",
     "2026-10-22 11:30:01", "SYSTEM"),
]

# 완료 건(#1)의 처리안 승인·실행 기록. 이슈대장과 RESOLUTION_LOG가 서로 맞도록 넣는다.
RESOLUTIONS = [
    (1, "실행 성공", "[매핑 변경] V002→103 생성, #1 완료",
     '{"issue_no": 1, "mapping_change": {"kind": "create", "vessel_id": "V002", "action_seq": "103"}, '
     '"new_status": "완료"}', "A001", "2026-10-21 13:50:00"),
]


def init_db(path: Path | str | None = None) -> None:
    """모든 테이블을 지우고 다시 만든 뒤 시드 값을 넣는다. path를 비우면 config.db_path()."""
    path = path or db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        # 지우는 순서를 신경 쓰지 않도록 FOREIGN KEY를 잠시 끈다.
        conn.execute("PRAGMA foreign_keys = OFF")
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        for name in tables:
            conn.execute(f"DROP TABLE {name}")
        # ISSUE 번호가 1부터 다시 시작하도록 자동 증가 카운터도 비운다.
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone():
            conn.execute("DELETE FROM sqlite_sequence")
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")

        conn.executescript(SCHEMA)
        with conn:
            conn.executemany("INSERT INTO VESSEL_MST VALUES (?, ?)", VESSELS)
            conn.executemany("INSERT INTO ACTION_MST VALUES (?, ?)", ACTIONS)
            conn.executemany("INSERT INTO VESSEL_ACTION VALUES (?, ?, ?, ?)", MAPPINGS)
            conn.executemany("INSERT INTO USER_MST VALUES (?, ?, ?)", USERS)
            conn.executemany("INSERT INTO OWNER_MST VALUES (?, ?, ?)", OWNERS)
            conn.executemany(
                "INSERT INTO ISSUE (RECEIVED_AT, REQUESTER_ID, VESSEL_ID, ACTION_SEQ, INQUIRY, OWNER_ROLE, STATUS,"
                " RESULT, PROCESSED_AT, PROCESSOR_ID) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ISSUES)
            conn.executemany(
                "INSERT INTO RESOLUTION_LOG (ISSUE_NO, KIND, CONTENT, PROPOSAL_JSON, USER_ID, LOGGED_AT)"
                " VALUES (?, ?, ?, ?, ?, ?)", RESOLUTIONS)
    finally:
        conn.close()


def reset_checkpoints(path: Path | str | None = None) -> None:
    """비서 대화 체크포인터 DB를 지운다. 이슈 번호가 1부터 다시 시작하므로 옛 대화가 새 이슈에 붙지 않게 한다."""
    path = Path(path or checkpoint_db_path())
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


if __name__ == "__main__":
    init_db()
    reset_checkpoints()
    print(f"시드 완료: {db_path()}")
