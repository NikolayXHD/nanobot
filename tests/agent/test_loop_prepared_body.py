from __future__ import annotations

from pathlib import Path

from nanobot.agent.loop import AgentLoop, TurnContext, TurnKind
from nanobot.agent.turn_delivery import TurnDeliveryFactory
from nanobot.bus.events import PREPARED_BODY_PATH_META, InboundMessage
from nanobot.bus.queue import MessageBus


async def test_when_body_is_declared_then_final_content_is_replaced(tmp_path: Path) -> None:
    declared_body_path = tmp_path / "run-1.md"
    declared_body_path.write_text("prepared body text", encoding="utf-8")
    loop, ctx = _turn(tmp_path, str(declared_body_path))

    await loop._apply_prepared_body(ctx)

    assert ctx.final_content == "prepared body text"
    assert ctx.suppress_response is False


async def test_when_declared_body_is_missing_then_error_text_is_delivered(tmp_path: Path) -> None:
    loop, ctx = _turn(tmp_path, str(tmp_path / "missing.md"))

    await loop._apply_prepared_body(ctx)

    assert "missing" in str(ctx.final_content)
    assert ctx.suppress_response is False


async def test_when_turn_failed_then_its_error_text_is_kept(tmp_path: Path) -> None:
    loop, ctx = _turn(tmp_path, str(tmp_path / "missing.md"))
    ctx.stop_reason = "error"
    ctx.final_content = "Error calling LLM: Connection error."

    await loop._apply_prepared_body(ctx)

    assert ctx.final_content == "Error calling LLM: Connection error."


async def test_when_body_file_appears_without_declaration_then_final_content_is_replaced(
    tmp_path: Path,
) -> None:
    loop, ctx = _turn(tmp_path)
    fallback_body_path = tmp_path / "outbox" / f"{ctx.turn_id}.md"
    fallback_body_path.parent.mkdir()
    fallback_body_path.write_text("scripted body", encoding="utf-8")

    await loop._apply_prepared_body(ctx)

    assert ctx.final_content == "scripted body"
    assert ctx.suppress_response is False


async def test_when_fallback_body_file_is_empty_then_error_text_is_delivered(tmp_path: Path) -> None:
    loop, ctx = _turn(tmp_path)
    fallback_body_path = tmp_path / "outbox" / f"{ctx.turn_id}.md"
    fallback_body_path.parent.mkdir()
    fallback_body_path.touch()

    await loop._apply_prepared_body(ctx)

    assert "no text content" in str(ctx.final_content)
    assert ctx.suppress_response is False


async def test_when_turn_has_no_body_file_then_model_reply_is_kept(tmp_path: Path) -> None:
    loop, ctx = _turn(tmp_path)

    await loop._apply_prepared_body(ctx)

    assert ctx.final_content is None
    assert ctx.suppress_response is False


def _turn(tmp_path: Path, declared_body_path: str | None = None) -> tuple[AgentLoop, TurnContext]:
    loop = AgentLoop.__new__(AgentLoop)
    loop.workspace = tmp_path
    msg = InboundMessage(
        channel="email",
        sender_id="cron",
        chat_id="bot@example.com",
        content="run the task",
        metadata={PREPARED_BODY_PATH_META: declared_body_path} if declared_body_path else {},
    )
    factory = TurnDeliveryFactory(MessageBus())
    ctx = TurnContext(
        msg=msg,
        session_key="email:bot@example.com",
        turn_id="turn-1",
        runtime=None,
        kind=TurnKind.USER,
        delivery=factory.create(msg, "email:bot@example.com"),
    )
    return loop, ctx
