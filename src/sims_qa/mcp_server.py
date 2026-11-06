"""Standard MCP stdio entry point; shares the same workflow as FastAPI."""

from mcp.server.fastmcp import FastMCP

from .api import build_workflow
from .settings import Settings


mcp = FastMCP("sims-project-qa")
settings = Settings()
workflow = build_workflow(settings)


@mcp.tool()
async def ask_sims_project(
    question: str, session_id: str | None = None, allow_exploration: bool = False
) -> dict[str, str | bool | None]:
    """查询 SIMS project；返回回答、澄清问题或错误及其类型。"""
    result = await workflow.ask_result(
        question, session_id=session_id, allow_exploration=allow_exploration
    )
    return {
        "type": result.type,
        "text": result.text,
        "request_id": result.request_id,
        "code": result.code,
        "is_exploratory": result.is_exploratory,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
