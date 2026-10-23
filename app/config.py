"""설정: .env에서 값을 읽는다."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "app.db"
CHECKPOINT_DB_PATH = ROOT / "data" / "checkpoints.db"
TRACES_DIR = ROOT / "traces"

# KST는 서머타임이 없으므로 고정 +9시간으로 둔다.
KST = timezone(timedelta(hours=9))

load_dotenv(ROOT / ".env")


def db_path() -> Path:
    """앱 DB 경로. APP_DB_PATH가 있으면 그 값을 쓴다(테스트·MCP 서버 하위 프로세스용)."""
    return Path(os.getenv("APP_DB_PATH") or DB_PATH)


def checkpoint_db_path() -> Path:
    """비서 대화 체크포인터 DB 경로. CHECKPOINT_DB_PATH가 있으면 그 값을 쓴다."""
    return Path(os.getenv("CHECKPOINT_DB_PATH") or CHECKPOINT_DB_PATH)


def bedrock_model_ids() -> list[str]:
    """BEDROCK_MODEL_IDS(쉼표 구분)를 우선순위 순서의 목록으로 돌려준다."""
    raw = os.getenv("BEDROCK_MODEL_IDS", "")
    return [m.strip() for m in raw.split(",") if m.strip()]


def aws_region() -> str | None:
    return os.getenv("AWS_DEFAULT_REGION")


def llm_mode() -> str:
    """LLM_MODE: fake(기본, 가짜 LLM) 또는 bedrock(실제 호출). 평소 개발은 fake만 쓴다."""
    return os.getenv("LLM_MODE", "fake").strip().lower()


def now_kst() -> str:
    """현재 시각을 KST 'YYYY-MM-DD HH:MM:SS' 문자열로 돌려준다."""
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
