from __future__ import annotations

from pathlib import Path

from nanobot.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from nanobot.agent.tools.reply_body import ReplyBodyTool


async def test_reply_body_tool_creates_the_turn_body_file(tmp_path: Path) -> None:
    result = await _execute(tmp_path)

    body_path = tmp_path / "outbox" / "turn-9.md"
    assert body_path.is_file()
    assert str(body_path) in result
    assert "NANOBOT_BODY_PATH" in result


async def test_reply_body_tool_requires_an_active_turn() -> None:
    result = await ReplyBodyTool().execute()

    assert "Error" in result


async def _execute(tmp_path: Path) -> str:
    tool = ReplyBodyTool()
    request = RequestContext(
        channel="websocket",
        chat_id="chat-1",
        turn_id="turn-9",
        workspace=tmp_path,
    )
    token = bind_request_context(request)
    try:
        return await tool.execute()
    finally:
        reset_request_context(token)
