from __future__ import annotations

from pathlib import Path

from nanobot.agent.loop import AgentLoop, TurnContext, TurnKind
from nanobot.agent.turn_delivery import TurnDeliveryFactory
from nanobot.bus.events import PREPARED_BODY_PATH_META, InboundMessage
from nanobot.bus.queue import MessageBus


async def test_prepared_body_replaces_final_content(tmp_path: Path) -> None:
    body_path = tmp_path / "run-1.md"
    body_path.write_text("prepared body text", encoding="utf-8")
    loop, ctx = _context(tmp_path, str(body_path))

    await loop._apply_prepared_body(ctx)

    assert ctx.final_content == "prepared body text"
    assert ctx.suppress_response is False


async def test_invalid_prepared_body_reports_the_error(tmp_path: Path) -> None:
    loop, ctx = _context(tmp_path, str(tmp_path / "missing.md"))

    await loop._apply_prepared_body(ctx)

    assert ctx.suppress_response is False
    assert "missing" in str(ctx.final_content)


async def test_turn_body_file_replaces_final_content(tmp_path: Path) -> None:
    loop, ctx = _context(tmp_path)
    body_path = tmp_path / "outbox" / f"{ctx.turn_id}.md"
    body_path.parent.mkdir()
    body_path.write_text("scripted body", encoding="utf-8")

    await loop._apply_prepared_body(ctx)

    assert ctx.final_content == "scripted body"
    assert ctx.suppress_response is False


async def test_empty_turn_body_file_reports_the_error(tmp_path: Path) -> None:
    loop, ctx = _context(tmp_path)
    body_path = tmp_path / "outbox" / f"{ctx.turn_id}.md"
    body_path.parent.mkdir()
    body_path.touch()

    await loop._apply_prepared_body(ctx)

    assert "no text content" in str(ctx.final_content)
    assert ctx.suppress_response is False


async def test_turn_without_a_body_file_keeps_the_reply(tmp_path: Path) -> None:
    loop, ctx = _context(tmp_path)

    await loop._apply_prepared_body(ctx)

    assert ctx.final_content is None
    assert ctx.suppress_response is False


def _context(tmp_path: Path, body_path: str | None = None) -> tuple[AgentLoop, TurnContext]:
    loop = AgentLoop.__new__(AgentLoop)
    loop.workspace = tmp_path
    msg = InboundMessage(
        channel="email",
        sender_id="cron",
        chat_id="bot@example.com",
        content="run the task",
        metadata={PREPARED_BODY_PATH_META: body_path} if body_path else {},
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
