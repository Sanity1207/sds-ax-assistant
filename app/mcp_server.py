"""FastMCP stdio 서버. 실행: python -m app.mcp_server

DB 경로는 환경변수 APP_DB_PATH(없으면 data/app.db)로 받는다. 클라이언트는 조사 비서 하나뿐이다.
"""

import sys

from mcp.server.fastmcp import FastMCP

from app.tools import TOOL_SPECS, TOOLS_IMPL


def build_server() -> FastMCP:
    server = FastMCP("sds-ax-assistant", log_level="WARNING")
    for name, fn in TOOLS_IMPL.items():
        # 반환은 사람이 읽는 문자열 하나이므로 구조화 출력은 끈다.
        server.add_tool(fn, name=name, description=TOOL_SPECS[name]["description"], structured_output=False)
    return server


def server_command() -> tuple[str, list[str]]:
    """클라이언트가 이 서버를 띄울 명령(지금 쓰는 파이썬으로 실행)."""
    return sys.executable, ["-m", "app.mcp_server"]


if __name__ == "__main__":
    build_server().run("stdio")
