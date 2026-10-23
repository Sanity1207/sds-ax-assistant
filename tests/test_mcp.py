import asyncio
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import ROOT
from app.mcp_server import server_command
from app.tools import TOOL_SPECS, TOOLS_IMPL, find_vessel


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
    assert tools == ["find_vessel"]
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
