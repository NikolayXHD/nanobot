"""Let a script produce the user-visible reply for the current turn."""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

from typing import Any

from nanobot.agent.prepared_body import PREPARED_BODY_MAX_BYTES, turn_body_path
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context
from nanobot.agent.tools.schema import tool_parameters_schema


@tool_parameters(tool_parameters_schema())
class ReplyBodyTool(Tool):
    """Declare that this turn's reply is a file written by a script.

    Creating the empty file is the declaration: the turn then requires the
    reply body, and an empty or unreadable file fails the turn.
    """

    @property
    def name(self) -> str:
        return "reply_body"

    @property
    def description(self) -> str:
        return (
            "Declare that the user-visible reply of this turn is a file written by a "
            "script instead of your own text. Use it when the user asks for a generated "
            "report, exact formatting, or data rendered verbatim. The call creates the "
            "reply body file and returns its path, also available to commands as "
            "NANOBOT_BODY_PATH. Write non-empty UTF-8 text there: when the turn "
            "ends, the file becomes the user-visible message, and the turn fails if "
            "the file has no text."
        )

    async def execute(self, **kwargs: Any) -> str:
        ctx = current_request_context()
        if ctx is None or ctx.turn_id is None or ctx.workspace is None:
            return ToolResult.error("Error: no active turn to attach a reply body to")
        body_path = turn_body_path(ctx.workspace, ctx.turn_id)
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.touch()
        max_mib = PREPARED_BODY_MAX_BYTES // (1024 * 1024)
        return (
            f"Reply body file: {body_path}\n"
            f"Commands receive its path in NANOBOT_BODY_PATH. Write non-empty UTF-8 "
            f"text of at most {max_mib} MiB there, then end the turn: the file "
            "becomes the user-visible message, and the turn fails if the file has "
            "no text. Do not print the body."
        )
