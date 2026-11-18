from __future__ import annotations

from pathlib import Path

from nanobot.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from nanobot.agent.tools.reply_body import ReplyBodyTool


async def test_when_turn_is_active_then_body_file_is_created_and_path_returned(tmp_path: Path) -> None:
    request = RequestContext(
        channel="websocket",
        chat_id="chat-1",
        turn_id="turn-9",
        workspace=tmp_path,
    )
    token = bind_request_context(request)
    try:
        result = await ReplyBodyTool().execute()
    finally:
        reset_request_context(token)

    body_file = tmp_path / "outbox" / "turn-9.md"
    assert body_file.is_file()
    assert str(body_file) in result
    assert "NANOBOT_BODY_PATH" in result


async def test_when_no_active_turn_then_error_is_returned() -> None:
    result = await ReplyBodyTool().execute()

    assert "Error" in result
