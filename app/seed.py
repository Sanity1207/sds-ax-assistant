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
