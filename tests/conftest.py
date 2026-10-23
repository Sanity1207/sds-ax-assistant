import os

import pytest

# pytest는 늘 가짜 LLM을 쓴다. .env에 LLM_MODE=bedrock이 있어도 여기서 덮는다.
os.environ["LLM_MODE"] = "fake"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """임시 DB를 시드 상태로 만들고 APP_DB_PATH로 가리킨다(MCP 서버 하위 프로세스도 이 값을 받는다)."""
    from app.seed import init_db

    path = tmp_path / "app.db"
    init_db(path)
    monkeypatch.setenv("APP_DB_PATH", str(path))
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "checkpoints.db"))
    return path
