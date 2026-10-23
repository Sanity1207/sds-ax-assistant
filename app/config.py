"""설정: .env에서 값을 읽는다."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "app.db"
TRACES_DIR = ROOT / "traces"

load_dotenv(ROOT / ".env")


def bedrock_model_ids() -> list[str]:
    """BEDROCK_MODEL_IDS(쉼표 구분)를 우선순위 순서의 목록으로 돌려준다."""
    raw = os.getenv("BEDROCK_MODEL_IDS", "")
    return [m.strip() for m in raw.split(",") if m.strip()]


def aws_region() -> str | None:
    return os.getenv("AWS_DEFAULT_REGION")
