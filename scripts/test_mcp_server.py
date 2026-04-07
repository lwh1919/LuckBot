"""本地测试用 MCP 服务端（stdio）。

启动方式（项目根目录）：
  .venv/bin/python scripts/test_mcp_server.py
"""

from __future__ import annotations

from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("luckbot-test-local")


@mcp.tool()
def ping(text: str = "pong") -> str:
    """回显输入文本，便于快速连通性测试。"""
    return f"连通成功: {text}"


@mcp.tool()
def utc_now() -> str:
    """返回当前 UTC 时间（ISO-8601）。"""
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    mcp.run(transport="stdio")
